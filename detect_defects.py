"""Automatische Zell- und Defektkandidaten fuer aufrechte CT-Zellansichten."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Bild nicht lesbar: {path}')
    return image


def prepare_image(image, ignored_lines=()):
    spread = np.ptp(image.astype(np.int16), axis=2)
    excluded = (spread > 25).astype(np.uint8) * 255
    excluded = cv2.dilate(excluded, np.ones((3, 3), np.uint8))
    for start_x, start_y, end_x, end_y, width in ignored_lines:
        if width < 1:
            raise ValueError('Linienbreite muss positiv sein.')
        cv2.line(excluded, (start_x, start_y), (end_x, end_y), 255, width)
    cleaned = cv2.inpaint(image, excluded, 3, cv2.INPAINT_TELEA)
    return cleaned, excluded


def segment_cell(image, rectangle):
    left, top, width, height = rectangle
    image_height, image_width = image.shape[:2]
    if min(width, height) < 20 or left < 1 or top < 1:
        raise ValueError('Zellrechteck muss mindestens 20 Pixel gross sein und innerhalb des Bildes liegen.')
    if left + width >= image_width or top + height >= image_height:
        raise ValueError('Um das Zellrechteck muss ein Bildrand verbleiben.')
    labels = np.full(image.shape[:2], cv2.GC_BGD, np.uint8)
    labels[top:top + height, left:left + width] = cv2.GC_PR_FGD
    inner_left, inner_top = left + width // 3, top + height // 4
    labels[inner_top:top + 3 * height // 4, inner_left:left + 2 * width // 3] = cv2.GC_FGD
    spine_left, spine_right = left + 9 * width // 20, left + 11 * width // 20
    labels[top + height // 30:top + 29 * height // 30, spine_left:spine_right] = cv2.GC_FGD
    background = np.zeros((1, 65), np.float64)
    foreground = np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(0)
    cv2.grabCut(image, labels, None, background, foreground, 5, cv2.GC_INIT_WITH_MASK)
    mask = np.isin(labels, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError('Keine Zellflaeche erkannt.')
    mask[:] = 0
    cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 255, cv2.FILLED)
    return mask


def dark_score(image, scale):
    if scale < 5 or scale % 2 == 0:
        raise ValueError('Suchgroesse muss ungerade und mindestens 5 sein.')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    smoothed = cv2.GaussianBlur(gray, (0, 0), 1.5)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (scale, scale))
    return cv2.morphologyEx(smoothed, cv2.MORPH_BLACKHAT, kernel)


def segment_defect(score, cell_mask, excluded, point, threshold):
    if not 0 < threshold < 255:
        raise ValueError('Kontrastschwelle muss zwischen 0 und 255 liegen.')
    column, row = point
    if not (0 <= row < score.shape[0] and 0 <= column < score.shape[1]):
        raise ValueError('Defektpunkt liegt ausserhalb des Bildes.')
    if not cell_mask[row, column] or excluded[row, column]:
        raise ValueError('Defektpunkt liegt ausserhalb der Zelle oder auf einer ausgeschlossenen Messlinie.')
    interior = cv2.erode(cell_mask, np.ones((7, 7), np.uint8))
    candidate = ((score >= threshold) & (interior > 0) & (excluded == 0)).astype(np.uint8)
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    _, labels, statistics, _ = cv2.connectedComponentsWithStats(candidate, 8)
    selected_label = labels[row, column]
    if selected_label == 0 or statistics[selected_label, cv2.CC_STAT_AREA] < 9:
        raise ValueError('Am Defektpunkt kein dunkler Fleck erkannt. Punkt oder Kontrastschwelle pruefen.')
    return (labels == selected_label).astype(np.uint8) * 255


def measure_mask(mask, cell_mask, pixel_size=None):
    coordinates = cv2.findNonZero(mask)
    if coordinates is None:
        raise ValueError('Leere Defektmaske kann nicht vermessen werden.')
    (_, _), (first_extent, second_extent), _ = cv2.minAreaRect(coordinates)
    long_extent, short_extent = sorted((first_extent + 1, second_extent + 1), reverse=True)
    area = int(np.count_nonzero(mask))
    cell_area = int(np.count_nonzero(cell_mask))
    result = {
        'area_px2': area,
        'length_px': round(long_extent, 2),
        'width_px': round(short_extent, 2),
        'cell_area_px2': cell_area,
        'cell_fraction_percent': round(100 * area / cell_area, 3),
        'size_definition': 'Seiten des minimalen gedrehten Begrenzungsrechtecks, Pixelzentren plus 1 Pixel',
    }
    if pixel_size is not None:
        if not np.isfinite(pixel_size) or pixel_size <= 0:
            raise ValueError('Pixelgroesse muss positiv und endlich sein.')
        result.update(area_mm2=area * pixel_size ** 2, length_mm=long_extent * pixel_size,
                      width_mm=short_extent * pixel_size)
    return result


def render_overlay(image, cell_mask, defect_mask):
    overlay = image.copy()
    cell_pixels = cell_mask > 0
    overlay[cell_pixels] = np.round(
        0.65 * image[cell_pixels] + 0.35 * np.array([30, 210, 45])
    ).astype(np.uint8)
    overlay[defect_mask > 0] = (0, 0, 255)
    return overlay


def select_regions(image):
    scale = min(1.0, 1000 / image.shape[1], 780 / image.shape[0])
    preview = cv2.resize(image, None, fx=scale, fy=scale)
    print('Zellen einzeln mit Rechtecken umschliessen: Enter bestaetigt, Esc beendet die Auswahl.')
    rectangles = cv2.selectROIs('Zellbereiche', preview, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    if len(rectangles) == 0:
        raise ValueError('Keine Zellbereiche ausgewaehlt.')
    rectangles = [tuple(int(round(value / scale)) for value in rectangle) for rectangle in rectangles]
    points = []

    def on_click(event, column, row, flags, userdata):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((int(round(column / scale)), int(round(row / scale))))
            cv2.circle(preview, (column, row), 4, (0, 0, 255), 1)

    window = 'Dunkle Defekte waehlen'
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_click)
    print('Je einen dunklen Punkt pro Defekt anklicken. Enter bestaetigt, Esc bricht ab.')
    try:
        while True:
            cv2.imshow(window, preview)
            key = cv2.waitKey(30) & 0xFF
            if key == 13:
                break
            if key == 27 or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                raise ValueError('Auswahl abgebrochen.')
    finally:
        cv2.destroyAllWindows()
    if not points:
        raise ValueError('Kein Defektpunkt ausgewaehlt.')
    return rectangles, points


def analyze(image, rectangles, points, scale=81, threshold=8, ignored_lines=(), pixel_size=None):
    cleaned, excluded = prepare_image(image, ignored_lines)
    cells = [segment_cell(cleaned, rectangle) for rectangle in rectangles]
    combined_cells = np.maximum.reduce(cells)
    score = dark_score(cleaned, scale)
    combined_defects = np.zeros(image.shape[:2], np.uint8)
    measurements = []
    for point in points:
        column, row = point
        if not (0 <= row < image.shape[0] and 0 <= column < image.shape[1]):
            raise ValueError('Defektpunkt liegt ausserhalb des Bildes.')
        matches = [index for index, mask in enumerate(cells) if mask[row, column]]
        if len(matches) != 1:
            raise ValueError('Defektpunkt muss genau einer Zellmaske zugeordnet sein.')
        cell_index = matches[0]
        defect = segment_defect(score, cells[cell_index], excluded, point, threshold)
        if np.any((defect > 0) & (combined_defects > 0)):
            continue
        combined_defects |= defect
        measurement = measure_mask(defect, cells[cell_index], pixel_size)
        measurement.update(cell_index=cell_index + 1, seed=list(point), review_required=True)
        measurements.append(measurement)
    return combined_cells, combined_defects, excluded, measurements


def find_measurement_lines(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    highlights = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, np.ones((9, 9), np.uint8))
    bright_lines = ((highlights > 45) & (gray > 155)).astype(np.uint8) * 255
    minimum_length = max(40, min(gray.shape) // 8)
    segments = cv2.HoughLinesP(bright_lines, 1, np.pi / 180, 25,
                               minLineLength=minimum_length, maxLineGap=30)
    ignored_lines = []
    if segments is not None:
        for start_x, start_y, end_x, end_y in segments.reshape(-1, 4):
            angle = abs(np.degrees(np.arctan2(end_y - start_y, end_x - start_x))) % 180
            if 5 < angle < 85 or 95 < angle < 175:
                ignored_lines.append([int(start_x), int(start_y), int(end_x), int(end_y), 9])
    return ignored_lines


def automatic_cells(image):
    height, width = image.shape[:2]
    if min(height, width) < 80:
        raise ValueError('Bild ist fuer die automatische Zellsuche zu klein.')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    smooth = cv2.GaussianBlur(gray, (0, 0), max(1.5, min(height, width) / 200))
    margin_y, margin_x = int(height * 0.10), max(1, int(width * 0.03))
    interior = smooth[margin_y:height - margin_y, margin_x:width - margin_x]
    if float(np.std(interior)) < 3:
        raise ValueError('Keine ausreichend kontrastreichen Zellflaechen erkannt.')
    threshold, _ = cv2.threshold(interior, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = (smooth > threshold).astype(np.uint8) * 255
    binary[:margin_y] = binary[height - margin_y:] = 0
    binary[:, :margin_x] = binary[:, width - margin_x:] = 0
    kernel_size = max(3, int(min(height, width) * 0.008) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (max(3, int(width * 0.03) | 1), max(5, int(height * 0.12) | 1))
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cells = []
    for contour in contours:
        left, top, cell_width, cell_height = cv2.boundingRect(contour)
        if cell_height < 0.4 * height or cell_width < 0.12 * width:
            continue
        if cell_width > 0.8 * width or cell_height < 1.2 * cell_width:
            continue
        if cv2.contourArea(contour) < 0.04 * height * width:
            continue
        mask = np.zeros_like(gray)
        cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
        cells.append((left, mask))
    if not cells:
        raise ValueError('Keine plausiblen Zellflaechen erkannt. CT-Zuschnitt und Kontrast pruefen.')
    return [mask for _, mask in sorted(cells, key=lambda item: item[0])]


def automatic_defects(score, cell_mask, excluded, threshold):
    if not np.isfinite(threshold) or not 0 < threshold < 255:
        raise ValueError('Kontrastschwelle muss endlich und zwischen 0 und 255 liegen.')
    cell_area = np.count_nonzero(cell_mask)
    border = max(5, int(np.sqrt(cell_area) * 0.02) | 1)
    interior = cv2.erode(cell_mask, np.ones((border, border), np.uint8))
    candidates = ((score >= threshold) & (interior > 0) & (excluded == 0)).astype(np.uint8)
    candidates = cv2.morphologyEx(candidates, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    _, labels, statistics, _ = cv2.connectedComponentsWithStats(candidates, 8)
    defects = []
    for label_index in range(1, len(statistics)):
        _, _, width, height, area = statistics[label_index]
        if area < max(20, cell_area * 0.001) or min(width, height) < 4:
            continue
        defects.append((labels == label_index).astype(np.uint8) * 255)
    return defects


def prepare_automatic(image, scale=81, ignored_lines=()):
    detected_lines = find_measurement_lines(image)
    cleaned, excluded = prepare_image(image, list(ignored_lines) + detected_lines)
    cells = automatic_cells(cleaned)
    score = dark_score(cleaned, scale)
    return cells, score, excluded, detected_lines


def evaluate_automatic(prepared, threshold=8, pixel_size=None):
    cells, score, excluded, detected_lines = prepared
    combined_defects = np.zeros(score.shape, np.uint8)
    measurements = []
    for cell_index, cell in enumerate(cells, start=1):
        for defect in automatic_defects(score, cell, excluded, threshold):
            combined_defects |= defect
            measurement = measure_mask(defect, cell, pixel_size)
            measurement.update(cell_index=cell_index, review_required=True)
            measurements.append(measurement)
    metadata = {
        'cell_count': len(cells), 'detected_measurement_lines': detected_lines,
        'candidate_count': len(measurements),
        'review_required': True,
        'warning': 'Keine Kandidaten bedeutet nicht Fehlerfreiheit. Zellkonturen und Kandidaten pruefen.',
    }
    return np.maximum.reduce(cells), combined_defects, excluded, measurements, metadata


def analyze_automatic(image, scale=81, threshold=8, ignored_lines=(), pixel_size=None):
    return evaluate_automatic(prepare_automatic(image, scale, ignored_lines), threshold, pixel_size)


def save_results(output, image, cells, defects, excluded, report):
    output.mkdir(parents=True, exist_ok=False)
    outputs = {
        'overlay.png': render_overlay(image, cells, defects),
        'cells_mask.png': cells,
        'defects_mask.png': defects,
        'excluded_mask.png': excluded,
    }
    for name, contents in outputs.items():
        success, encoded = cv2.imencode('.png', contents)
        if not success:
            raise ValueError(f'Bild konnte nicht kodiert werden: {name}')
        encoded.tofile(output / name)
    (output / 'measurements.json').write_text(json.dumps(report, indent=4, ensure_ascii=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='Unmarkiertes Original oder CT-Ausschnitt')
    parser.add_argument('--cell', type=int, nargs=4, action='append', help='Zellrechteck: links oben breite hoehe')
    parser.add_argument('--point', type=int, nargs=2, action='append', help='Dunkler Defektpunkt: x y')
    parser.add_argument('--ignore-line', type=int, nargs=5, action='append', default=[], help='x1 y1 x2 y2 breite')
    parser.add_argument('--scale', type=int, default=81, help='Suchgroesse in Pixeln, ungerade')
    parser.add_argument('--threshold', type=float, default=8, help='Mindestkontrast in Graustufen')
    parser.add_argument('--pixel-size-mm', type=float, help='Kalibrierte isotrope Pixelgroesse in mm')
    parser.add_argument('--output', type=Path, default=Path('Defektanalyse'))
    parser.add_argument('--manual', action='store_true', help='Optional: Zellbereiche und Defektpunkte auswaehlen')
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError('Ausgabeordner existiert bereits. Bitte einen neuen Namen angeben.')
        if bool(args.cell) != bool(args.point):
            raise ValueError('--cell und --point gemeinsam angeben.')
        if args.pixel_size_mm is not None and (not np.isfinite(args.pixel_size_mm) or args.pixel_size_mm <= 0):
            raise ValueError('Pixelgroesse muss positiv und endlich sein.')
        image = read_image(args.image)
        rectangles, points, metadata = None, None, {}
        if args.manual or args.cell:
            rectangles, points = (args.cell, args.point) if args.cell else select_regions(image)
            cells, defects, excluded, measurements = analyze(
                image, rectangles, points, args.scale, args.threshold, args.ignore_line, args.pixel_size_mm
            )
        else:
            cells, defects, excluded, measurements, metadata = analyze_automatic(
                image, args.scale, args.threshold, args.ignore_line, args.pixel_size_mm
            )
        report = {
            'mode': 'manual' if rectangles else 'automatic', 'automatic_detection': metadata,
            'source': str(args.image), 'cell_rectangles': rectangles, 'defect_points': points,
            'scale_px': args.scale, 'threshold': args.threshold, 'ignored_lines': args.ignore_line,
            'pixel_size_mm': args.pixel_size_mm, 'defects': measurements,
            'status': 'Prototyp: Zellgrenzen und Defektkonturen fachlich pruefen; keine automatische Note.',
            'limitations': 'Ueberlagerungen verdecken Originaldaten. Ausgeschlossene Pixel werden nicht vermessen.',
        }
        save_results(args.output, image, cells, defects, excluded, report)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        print(f'Ergebnisse: {args.output.resolve()}')
    except (ValueError, OSError, cv2.error) as error:
        parser.exit(1, f'Fehler: {error}\n')


if __name__ == '__main__':
    main()