"""Gezielte Pruefungen der Masken, Messwerte und Farbdarstellung."""

import json
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from detect_defects import (
    analyze_automatic,
    automatic_cells,
    automatic_defects,
    dark_score,
    find_measurement_lines,
    measure_mask,
    render_overlay,
    segment_cell,
    segment_defect,
)
from defect_viewer import DefectViewer
from horizontal_lines import detect_horizontal_lines, render_horizontal_lines


class DefectTests(unittest.TestCase):
    def setUp(self):
        self.image = np.full((160, 160, 3), 140, np.uint8)
        cv2.circle(self.image, (60, 70), 12, (85, 85, 85), -1)
        cv2.circle(self.image, (105, 70), 12, (200, 200, 200), -1)
        self.cell = np.zeros((160, 160), np.uint8)
        self.cell[20:140, 20:140] = 255
        self.excluded = np.zeros_like(self.cell)

    def test_dark_not_bright(self):
        score = dark_score(self.image, 41)
        mask = segment_defect(score, self.cell, self.excluded, (60, 70), 20)
        self.assertGreater(np.count_nonzero(mask), 350)
        self.assertLess(np.count_nonzero(mask), 550)
        self.assertFalse(np.any(mask[self.cell == 0]))
        with self.assertRaises(ValueError):
            segment_defect(score, self.cell, self.excluded, (105, 70), 20)

    def test_overlay_colors(self):
        defect = np.zeros_like(self.cell)
        defect[60:70, 60:70] = 255
        overlay = render_overlay(self.image, self.cell, defect)
        np.testing.assert_array_equal(overlay[65, 65], [0, 0, 255])
        self.assertGreater(int(overlay[30, 30, 1]), int(overlay[30, 30, 2]))
        np.testing.assert_array_equal(overlay[0, 0], self.image[0, 0])

    def test_measurement_and_calibration(self):
        mask = np.zeros_like(self.cell)
        mask[60:70, 60:80] = 255
        measurement = measure_mask(mask, self.cell, 0.1)
        self.assertEqual(measurement['area_px2'], 200)
        self.assertEqual(measurement['length_px'], 20)
        self.assertEqual(measurement['width_px'], 10)
        self.assertAlmostEqual(measurement['area_mm2'], 2)
        with self.assertRaises(ValueError):
            measure_mask(mask, self.cell, -1)

    def test_excluded_point_and_empty_image(self):
        score = dark_score(self.image, 41)
        self.excluded[70, 60] = 255
        with self.assertRaises(ValueError):
            segment_defect(score, self.cell, self.excluded, (60, 70), 20)
        with self.assertRaises(ValueError):
            segment_defect(np.zeros_like(score), self.cell, self.excluded, (80, 80), 20)

    def test_automatic_cells_and_defects_without_clicks(self):
        image = np.full((320, 320, 3), 40, np.uint8)
        cv2.ellipse(image, (85, 160), (40, 115), 0, 0, 360, (165, 165, 165), -1)
        cv2.ellipse(image, (235, 160), (40, 115), 0, 0, 360, (165, 165, 165), -1)
        cv2.circle(image, (85, 105), 10, (85, 85, 85), -1)
        cv2.circle(image, (235, 105), 10, (215, 215, 215), -1)
        cells, defects, _, measurements, metadata = analyze_automatic(image, scale=41, threshold=20)
        self.assertEqual(metadata['cell_count'], 2)
        self.assertEqual(len(measurements), 1)
        self.assertEqual(defects[105, 85], 255)
        self.assertEqual(defects[105, 235], 0)
        self.assertFalse(np.any(defects[cells == 0]))
        self.assertTrue(metadata['review_required'])

    def test_measurement_line_array_shapes(self):
        segments = np.array([[20, 20, 90, 120]], dtype=np.int32)
        for shape in ((1, 4), (1, 1, 4)):
            with self.subTest(shape=shape):
                with patch('detect_defects.cv2.HoughLinesP', return_value=segments.reshape(shape)):
                    self.assertEqual(find_measurement_lines(self.image), [[20, 20, 90, 120, 9]])

    def test_automatic_no_defects_and_no_cells(self):
        image = np.full((320, 320, 3), 40, np.uint8)
        cv2.ellipse(image, (85, 160), (40, 115), 0, 0, 360, (165, 165, 165), -1)
        _, defects, _, measurements, _ = analyze_automatic(image, scale=41, threshold=20)
        self.assertEqual(np.count_nonzero(defects), 0)
        self.assertEqual(measurements, [])
        with self.assertRaises(ValueError):
            automatic_cells(np.full((320, 320, 3), 140, np.uint8))

    def test_cell_boundary(self):
        image = np.full((160, 160, 3), 30, np.uint8)
        cv2.ellipse(image, (80, 80), (30, 55), 0, 0, 360, (180, 180, 180), -1)
        mask = segment_cell(image, (40, 20, 80, 120))
        self.assertEqual(mask[80, 80], 255)
        self.assertEqual(mask[21, 41], 0)
        self.assertGreater(np.count_nonzero(mask), 4500)
        self.assertLess(np.count_nonzero(mask), 6000)

    def test_threshold_keeps_pixels_when_regions_merge(self):
        score = np.zeros_like(self.cell)
        score[60:75, 40:60] = 30
        score[60:75, 95:115] = 30
        score[64:71, 60:95] = 12
        previous = np.zeros_like(score)
        for threshold in range(254, 0, -1):
            with self.subTest(threshold=threshold):
                regions = automatic_defects(score, self.cell, self.excluded, threshold)
                current = np.zeros_like(score)
                for region in regions:
                    current |= region
                self.assertFalse(np.any((previous > 0) & (current == 0)))
                self.assertFalse(np.any(current[self.cell == 0]))
                previous = current
        self.assertEqual(previous[67, 75], 255)


class HorizontalLineTests(unittest.TestCase):
    def test_full_short_bright_lines_and_excluded_annotation(self):
        rows = np.arange(240, dtype=np.float32)[:, None]
        gray = np.repeat(130 + rows * 0.12, 240, axis=1)
        gray[59:62, 35:205] -= 25
        gray[99:102, 45:90] += 25
        gray[139:142, 35:205] += 25
        gray[179:182, 35:205] += 45
        image = cv2.cvtColor(gray.astype(np.uint8), cv2.COLOR_GRAY2BGR)
        mask = np.zeros((240, 240), np.uint8)
        mask[20:220, 30:210] = 255
        excluded = np.zeros_like(mask)
        excluded[176:185] = 255
        lines = detect_horizontal_lines(image, [mask], excluded)
        for expected, polarity in ((100, 'hell'), (140, 'hell')):
            matches = [line for line in lines if abs(line['z_px'] - expected) <= 1
                       and line['polarity'] == polarity]
            self.assertEqual(len(matches), 1, lines)
        self.assertFalse(any(abs(line['z_px'] - 180) < 6 for line in lines))
        self.assertFalse(any(abs(line['z_px'] - 60) < 6 for line in lines))
        self.assertTrue(all(line['polarity'] == 'hell' for line in lines))

    def test_smooth_background_and_small_spot_are_not_lines(self):
        image = np.full((240, 240, 3), 150, np.uint8)
        mask = np.zeros((240, 240), np.uint8)
        mask[20:220, 30:210] = 255
        cv2.circle(image, (120, 100), 4, (90, 90, 90), -1)
        self.assertEqual(detect_horizontal_lines(image, [mask], np.zeros_like(mask)), [])


class ViewerTests(unittest.TestCase):
    def wait_for_result(self, viewer):
        deadline = time.monotonic() + 20

        def check():
            if viewer.completed_key is not None or time.monotonic() > deadline:
                viewer.root.quit()
            else:
                viewer.root.after(20, check)

        viewer.root.after(20, check)
        viewer.root.mainloop()
        self.assertIsNotNone(viewer.completed_key, 'Zeitlimit bei der Bildauswertung.')

    def test_slider_navigation_errors_and_latest_result(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = np.full((320, 320, 3), 40, np.uint8)
            cv2.ellipse(image, (85, 160), (40, 115), 0, 0, 360, (165, 165, 165), -1)
            cv2.circle(image, (85, 105), 10, (85, 85, 85), -1)
            cv2.line(image, (55, 190), (115, 190), (190, 190, 190), 3)
            for name in ('bild2.png', 'bild10.png'):
                cv2.imencode('.png', image)[1].tofile(folder / name)
            cv2.imencode('.png', np.full_like(image, 40))[1].tofile(folder / 'bild20.png')
            root = tk.Tk()
            viewer = DefectViewer(root, folder)
            try:
                self.wait_for_result(viewer)
                self.assertIsNone(viewer.error)
                self.assertEqual(viewer.paths[0].name, 'bild2.png')
                self.assertGreater(np.count_nonzero(viewer.result[1]), 0)
                self.assertIsNotNone(viewer.photo)
                self.assertEqual(viewer.view_mode.get(), 'both')
                with patch('defect_viewer.render_overlay', wraps=render_overlay) as defect_render:
                    with patch('defect_viewer.render_horizontal_lines', wraps=render_horizontal_lines) as line_render:
                        viewer.draw()
                        defect_render.assert_called_once()
                        line_render.assert_called_once()
                        expected_overlay = render_overlay(viewer.image, viewer.result[0], viewer.result[1])
                        np.testing.assert_array_equal(line_render.call_args.args[0], expected_overlay)
                        defect_render.reset_mock()
                        line_render.reset_mock()
                        viewer.is_original.set(True)
                        viewer.draw()
                        defect_render.assert_not_called()
                        line_render.assert_not_called()
                        viewer.is_original.set(False)
                viewer.slider.set(200)
                self.wait_for_result(viewer)
                self.assertEqual(np.count_nonzero(viewer.result[1]), 0)
                self.assertEqual(len(viewer.cache), 1)
                viewer.move(1)
                viewer.slider.set(20)
                viewer.move(-1)
                viewer.move(1)
                self.wait_for_result(viewer)
                self.assertEqual(viewer.completed_key[1:3], (1, 20))
                self.assertGreater(len(viewer.line_table.get_children()), 0)
                expected_lines = viewer.result[4]['horizontal_lines']
                self.assertTrue(any(abs(line['z_px'] - 190) <= 2 for line in expected_lines))
                viewer.line_table.selection_set('0')
                viewer.draw()
                self.assertGreater(np.count_nonzero(viewer.result[1]), 0)
                root.geometry('700x520')
                root.update()
                viewer.is_original.set(True)
                viewer.draw()
                self.assertLessEqual(viewer.photo.width(), viewer.canvas.winfo_width())
                self.assertLessEqual(viewer.photo.height(), viewer.canvas.winfo_height())
                with patch('defect_viewer.filedialog.askdirectory', return_value=str(folder)):
                    viewer.export()
                report_path = next(folder.glob('bild10_*/measurements.json'))
                report = json.loads(report_path.read_text())
                self.assertEqual(report['threshold'], 20)
                self.assertEqual(Path(report['source']).name, 'bild10.png')
                self.assertTrue((report_path.parent / 'overlay.png').exists())
                self.assertEqual(report['horizontal_lines'], expected_lines)
                self.assertTrue(all(line['polarity'] == 'hell' for line in report['horizontal_lines']))
                self.assertEqual(report['line_prominence'], 3)
                with (report_path.parent / 'horizontal_lines.csv').open(encoding='utf-8-sig') as stream:
                    exported_lines = list(csv.DictReader(stream, delimiter=';'))
                self.assertEqual(len(exported_lines), len(expected_lines))
                self.assertTrue(all(line['polarity'] == 'hell' for line in exported_lines))
                self.assertEqual(int(exported_lines[0]['z_px']), expected_lines[0]['z_px'])
                viewer.line_slider.set(30)
                self.wait_for_result(viewer)
                self.assertEqual(viewer.completed_key[3], 30)
                self.assertEqual(viewer.threshold.get(), 20)
                self.assertTrue(all(line['prominence'] >= 30 for line in viewer.result[4]['horizontal_lines']))
                viewer.move(1)
                self.wait_for_result(viewer)
                self.assertIsNotNone(viewer.error)
                self.assertTrue(viewer.save_button.instate(['disabled']))
                self.assertTrue(viewer.next_button.instate(['disabled']))
                viewer.move(-1)
                self.wait_for_result(viewer)
                self.assertIsNone(viewer.error)
                self.assertEqual(viewer.threshold.get(), 20)
            finally:
                viewer.close()


if __name__ == '__main__':
    unittest.main()