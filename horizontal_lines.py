"""Helle Querlinienkandidaten aus horizontal gemittelten CT-Helligkeitsprofilen."""

import csv

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks


COORDINATE_SYSTEM = 'z_px = Bildzeile im geladenen Ausschnitt; 0 oben, positiv nach unten; Originalpixel'


def strip_candidates(residual, gray, valid, left, right, cell_index, prominence):
    strip_valid = valid[:, left:right]
    counts = strip_valid.sum(axis=1)
    has_support = counts >= max(8, 0.7 * (right - left))
    profile = np.divide((residual[:, left:right] * strip_valid).sum(axis=1), counts,
                        out=np.zeros(residual.shape[0], np.float32), where=counts > 0)
    raw_profile = np.divide((gray[:, left:right] * strip_valid).sum(axis=1), counts,
                            out=np.zeros(residual.shape[0], np.float32), where=counts > 0)
    ranges = np.flatnonzero(np.diff(np.r_[False, has_support, False]))
    candidates = []
    for start, stop in ranges.reshape(-1, 2):
        if stop - start < 12:
            continue
        signal = -profile[start:stop]
        peaks, properties = find_peaks(signal, prominence=prominence, distance=6, width=(1, 12))
        for peak_index, peak in enumerate(peaks):
            row = int(start + peak)
            if peak < 4 or stop - row <= 4 or signal[peak] < prominence:
                continue
            radius = max(4, int(np.ceil(properties['widths'][peak_index])))
            if row - radius < start or row + radius >= stop:
                continue
            flank_level = max(raw_profile[row - radius], raw_profile[row + radius])
            if raw_profile[row] - flank_level < max(1, prominence * 0.3):
                continue
            coherent = (-residual[row, left:right] >= max(1, signal[peak] * 0.4))
            support = np.count_nonzero(coherent & strip_valid[row]) / counts[row]
            if support < 0.55:
                continue
            candidates.append({
                'cell_index': cell_index, 'z_px': row, 'polarity': 'hell',
                'x_start_px': left, 'x_end_px': right - 1,
                'prominence': round(float(properties['prominences'][peak_index]), 3),
                'profile_width_px': round(float(properties['widths'][peak_index]), 2),
                'support_fraction': round(float(support), 3), 'review_required': True,
            })
    return candidates


def detect_horizontal_lines(image, cells, excluded, prominence=3):
    if not np.isfinite(prominence) or prominence <= 0:
        raise ValueError('Linienkontrast muss positiv und endlich sein.')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    candidates = []
    for cell_index, cell in enumerate(cells, start=1):
        valid = (cv2.erode(cell, np.ones((7, 7), np.uint8)) > 0) & (excluded == 0)
        weights = gaussian_filter1d(valid.astype(np.float32), 8, axis=0)
        background = np.divide(gaussian_filter1d(gray * valid, 8, axis=0), weights,
                               out=gray.copy(), where=weights > 0.01)
        residual = background - gray
        left, _, width, _ = cv2.boundingRect(cell)
        strip_width = max(16, width // 4)
        if width < strip_width:
            continue
        starts = list(range(left, left + width - strip_width + 1, max(1, strip_width // 2)))
        starts.append(left + width - strip_width)
        for strip_left in sorted(set(starts)):
            candidates.extend(strip_candidates(residual, gray, valid, strip_left, strip_left + strip_width,
                                                cell_index, prominence))
    merged = []
    for candidate in sorted(candidates, key=lambda item: item['prominence'], reverse=True):
        duplicates = [item for item in merged if item['cell_index'] == candidate['cell_index']
                      and item['polarity'] == candidate['polarity'] and abs(item['z_px'] - candidate['z_px']) <= 3]
        if duplicates:
            continue
        merged.append(candidate)
    return sorted(merged, key=lambda item: (item['cell_index'], item['z_px'], item['polarity']))


def render_horizontal_lines(image, lines):
    overlay = image.copy()
    for line in lines:
        cv2.line(overlay, (line['x_start_px'], line['z_px']), (line['x_end_px'], line['z_px']), (255, 80, 180), 2)
    return overlay


def save_line_results(output, image, lines):
    fields = ['cell_index', 'z_px', 'polarity', 'x_start_px', 'x_end_px', 'prominence',
              'profile_width_px', 'support_fraction', 'review_required']
    with (output / 'horizontal_lines.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=';')
        writer.writeheader()
        writer.writerows(lines)
    success, encoded = cv2.imencode('.png', render_horizontal_lines(image, lines))
    if not success:
        raise ValueError('Querlinienbild konnte nicht kodiert werden.')
    encoded.tofile(output / 'horizontal_lines.png')