"""Lokale CT-Bildansicht mit Schwellwertregler und Bildnavigation."""

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

from detect_defects import evaluate_automatic, prepare_automatic, read_image, render_overlay, save_results
from horizontal_lines import (
    COORDINATE_SYSTEM,
    detect_horizontal_lines,
    render_horizontal_lines,
    save_line_results,
)


def image_paths(folder):
    paths = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {'.png', '.jpg', '.jpeg'}]
    return sorted(paths, key=lambda path: [int(part) if part.isdigit() else part.lower()
                                        for part in re.split(r'(\d+)', path.name)])


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.popup = widget, text, None
        widget.bind('<Enter>', self.show)
        widget.bind('<Leave>', self.hide)
        widget.bind('<ButtonPress>', self.hide)

    def show(self, event=None):
        if self.popup is not None:
            return
        self.popup = tk.Toplevel(self.widget)
        self.popup.overrideredirect(True)
        self.popup.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + 32}')
        ttk.Label(self.popup, text=self.text, padding=5).pack()

    def hide(self, event=None):
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None


class DefectViewer:
    def __init__(self, root, folder):
        self.root, self.folder = root, Path(folder)
        self.paths = image_paths(self.folder)
        if not self.paths:
            raise ValueError('Im Ordner wurden keine PNG- oder JPG-Bilder gefunden.')
        self.index, self.revision = 0, 0
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.cache = OrderedDict()
        self.future, self.debounce, self.poll_job, self.resize_job = None, None, None, None
        self.image, self.result, self.photo = None, None, None
        self.completed_key, self.error = None, None
        self.is_closed = False
        self.threshold = tk.DoubleVar(root, 8)
        self.threshold_text = tk.StringVar(root, '8')
        self.is_original = tk.BooleanVar(root, False)
        self.view_mode = tk.StringVar(root, 'both')
        self.line_threshold = tk.DoubleVar(root, 3)
        self.line_threshold_text = tk.StringVar(root, '3')
        self.status = tk.StringVar(root)
        self.position = tk.StringVar(root)
        self.filename = tk.StringVar(root)
        self.legend = tk.StringVar(root, 'Querlinienkandidaten | Fachliche Pruefung erforderlich')
        self.build_window()
        self.request_update()

    def build_window(self):
        self.root.title('CT-Defektanalyse')
        self.root.geometry('1180x900')
        self.root.minsize(700, 520)
        style = ttk.Style(self.root)
        style.configure('TLabel', font=('Bahnschrift', 10))
        style.configure('TButton', font=('Bahnschrift', 10), padding=5)
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill='x')
        self.previous_button = ttk.Button(toolbar, text='<', width=3, command=lambda: self.move(-1))
        self.previous_button.pack(side='left')
        Tooltip(self.previous_button, 'Vorheriges Bild')
        self.next_button = ttk.Button(toolbar, text='>', width=3, command=lambda: self.move(1))
        self.next_button.pack(side='left', padx=(4, 10))
        Tooltip(self.next_button, 'Naechstes Bild')
        ttk.Label(toolbar, textvariable=self.position, width=8).pack(side='left')
        labels = [re.sub(r'^.*Schublade ', 'Schublade ', path.stem) for path in self.paths]
        self.selector = ttk.Combobox(toolbar, values=labels, state='readonly', width=38)
        self.selector.pack(side='left', fill='x', expand=True)
        self.selector.bind('<<ComboboxSelected>>', self.select_image)
        self.save_button = ttk.Button(toolbar, text='Exportieren', command=self.export)
        self.save_button.pack(side='right', padx=(10, 0))
        controls = ttk.Frame(self.root, padding=(10, 4, 10, 8))
        controls.pack(fill='x')
        ttk.Label(controls, text='Defektkontrast').pack(side='left')
        self.slider = ttk.Scale(controls, from_=1, to=254, variable=self.threshold, command=self.threshold_changed)
        self.slider.pack(side='left', fill='x', expand=True, padx=12)
        ttk.Label(controls, textvariable=self.threshold_text, width=4).pack(side='left')
        ttk.Checkbutton(controls, text='Original', variable=self.is_original, command=self.draw).pack(side='left')
        self.build_line_controls()
        self.filename_label = ttk.Label(self.root, textvariable=self.filename, padding=(10, 0), wraplength=1100)
        self.filename_label.pack(fill='x')
        self.canvas = tk.Canvas(self.root, background='#242626', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=10, pady=8)
        self.canvas.bind('<Configure>', self.resize)
        self.build_line_table()
        self.status_label = ttk.Label(self.root, textvariable=self.status, padding=(10, 4), wraplength=1100)
        self.status_label.pack(fill='x')
        self.legend_label = ttk.Label(self.root, textvariable=self.legend, padding=(10, 2, 10, 8), wraplength=1100)
        self.legend_label.pack(fill='x')
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def build_line_controls(self):
        controls = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        controls.pack(fill='x')
        ttk.Radiobutton(controls, text='Beides', value='both', variable=self.view_mode,
                        command=self.draw).pack(side='left')
        ttk.Radiobutton(controls, text='Defekte', value='defects', variable=self.view_mode,
                        command=self.draw).pack(side='left')
        ttk.Radiobutton(controls, text='Querlinien', value='lines', variable=self.view_mode,
                        command=self.draw).pack(side='left', padx=(0, 12))
        ttk.Label(controls, text='Linienkontrast').pack(side='left')
        self.line_slider = ttk.Scale(controls, from_=1, to=30, variable=self.line_threshold,
                                     command=self.line_threshold_changed)
        self.line_slider.pack(side='left', fill='x', expand=True, padx=10)
        ttk.Label(controls, textvariable=self.line_threshold_text, width=4).pack(side='left')

    def build_line_table(self):
        frame = ttk.Frame(self.root, padding=(10, 0))
        frame.pack(fill='x')
        self.line_table = ttk.Treeview(frame, columns=('cell', 'z', 'kind', 'contrast'), show='headings', height=4)
        for column, title in (('cell', 'Zelle (links nach rechts)'), ('z', 'z [px], 0 oben, nach unten'),
                              ('kind', 'Linientyp'), ('contrast', 'Linienkontrast')):
            self.line_table.heading(column, text=title)
            self.line_table.column(column, width=160, minwidth=100, anchor='center')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.line_table.yview)
        self.line_table.configure(yscrollcommand=scrollbar.set)
        self.line_table.pack(side='left', fill='x', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.line_table.bind('<<TreeviewSelect>>', lambda event: self.draw())

    def line_threshold_changed(self, value=None):
        self.line_threshold_text.set(str(int(round(self.line_threshold.get()))))
        self.request_update()

    def threshold_changed(self, value=None):
        self.threshold_text.set(str(int(round(self.threshold.get()))))
        self.request_update()

    def move(self, offset):
        target = max(0, min(len(self.paths) - 1, self.index + offset))
        if target != self.index:
            self.index = target
            self.image, self.result = None, None
            self.canvas.delete('all')
            self.request_update()

    def select_image(self, event=None):
        self.move(self.selector.current() - self.index)

    def request_update(self):
        self.revision += 1
        self.completed_key = None
        self.error = None
        self.line_table.delete(*self.line_table.get_children())
        self.selector.current(self.index)
        self.position.set(f'{self.index + 1} / {len(self.paths)}')
        self.filename.set(self.paths[self.index].name)
        self.previous_button.state(['disabled'] if self.index == 0 else ['!disabled'])
        self.next_button.state(['disabled'] if self.index == len(self.paths) - 1 else ['!disabled'])
        self.save_button.state(['disabled'])
        self.status.set('Auswertung ...')
        if self.debounce is not None:
            self.root.after_cancel(self.debounce)
        self.debounce = self.root.after(120, self.submit)

    def compute(self, path, threshold, line_threshold):
        stat = path.stat()
        cache_key = (path, stat.st_mtime_ns, stat.st_size)
        if cache_key not in self.cache:
            image = read_image(path)
            try:
                prepared, error = prepare_automatic(image), None
                lines = detect_horizontal_lines(image, prepared[0], prepared[2], prominence=1)
            except (ValueError, cv2.error) as exception:
                prepared, error = None, str(exception)
                lines = []
            self.cache[cache_key] = (image, prepared, error, lines)
            while len(self.cache) > 3:
                self.cache.popitem(last=False)
        self.cache.move_to_end(cache_key)
        image, prepared, error, lines = self.cache[cache_key]
        result = evaluate_automatic(prepared, threshold) if prepared is not None else None
        if result is not None:
            result[4]['horizontal_lines'] = [line for line in lines if line['prominence'] >= line_threshold]
        return image, result, error

    def submit(self):
        self.debounce = None
        if self.future is not None:
            return
        key = (self.revision, self.index, int(round(self.threshold.get())), int(round(self.line_threshold.get())))
        self.future = self.executor.submit(self.compute, self.paths[self.index], key[2], key[3])
        self.poll_job = self.root.after(40, lambda: self.poll(key))

    def poll(self, key):
        self.poll_job = None
        if not self.future.done():
            self.poll_job = self.root.after(40, lambda: self.poll(key))
            return
        future, self.future = self.future, None
        if key[0] != self.revision:
            if self.debounce is None:
                self.submit()
            return
        try:
            self.image, self.result, self.error = future.result()
        except Exception as exception:
            self.image, self.result, self.error = None, None, str(exception)
        self.completed_key = key
        if self.error:
            self.status.set(f'Nicht auswertbar: {self.error}')
        else:
            cells, defects, _, measurements, metadata = self.result
            for line_index, line in enumerate(metadata['horizontal_lines']):
                self.line_table.insert('', 'end', iid=str(line_index), values=(
                    line['cell_index'], line['z_px'], line['polarity'], line['prominence']))
            self.status.set(f'{metadata["cell_count"]} Zellflaechen | {len(measurements)} Kandidaten | '
                            f'{np.count_nonzero(defects):,} Pixel Kandidatenflaeche | '
                            f'{len(metadata["horizontal_lines"])} Querlinien')
            self.save_button.state(['!disabled'])
        self.draw()

    def resize(self, event=None):
        available_width = max(200, self.root.winfo_width() - 30)
        self.filename_label.configure(wraplength=available_width)
        self.status_label.configure(wraplength=available_width)
        self.legend_label.configure(wraplength=available_width)
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(80, self.draw)

    def draw(self):
        self.resize_job = None
        if self.view_mode.get() == 'both':
            self.legend.set('Zellen: gruen | Defekte: rot | Helle Linien: violett | Auswahl: gelb | Pruefung erforderlich')
        elif self.view_mode.get() == 'lines':
            self.legend.set('Helle Querlinien: violett | Auswahl: gelb | Pruefung erforderlich')
        else:
            self.legend.set('Zellflaechen: gruen | Defektkandidaten: rot | Fachliche Pruefung erforderlich')
        self.canvas.delete('all')
        if self.image is None:
            return
        display = self.image
        if self.result is not None and not self.is_original.get():
            if self.view_mode.get() in ('defects', 'both'):
                display = render_overlay(display, self.result[0], self.result[1])
            if self.view_mode.get() in ('lines', 'both'):
                lines = self.result[4]['horizontal_lines']
                display = render_horizontal_lines(display, lines)
                selection = self.line_table.selection()
                if selection and int(selection[0]) < len(lines):
                    line = lines[int(selection[0])]
                    cv2.line(display, (line['x_start_px'], line['z_px']),
                             (line['x_end_px'], line['z_px']), (0, 230, 255), 3)
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        ratio = min(width / display.shape[1], height / display.shape[0])
        size = (max(1, round(display.shape[1] * ratio)), max(1, round(display.shape[0] * ratio)))
        preview = cv2.resize(display, size, interpolation=cv2.INTER_AREA)
        success, encoded = cv2.imencode('.png', preview)
        if success:
            self.photo = tk.PhotoImage(master=self.root, data=encoded.tobytes(), format='png')
            self.canvas.create_image(width // 2, height // 2, image=self.photo, anchor='center')

    def export(self):
        if self.completed_key is None or self.result is None or self.error:
            return
        destination = filedialog.askdirectory(parent=self.root, title='Zielordner auswaehlen')
        if not destination:
            return
        source = self.paths[self.index]
        output = Path(destination) / (source.stem + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        cells, defects, excluded, measurements, metadata = self.result
        report = {
            'source': str(source), 'mode': 'automatic', 'threshold': self.completed_key[2],
            'scale_px': 81, 'pixel_size_mm': None, 'defects': measurements,
            'automatic_detection': metadata, 'review_required': True,
            'horizontal_lines': metadata['horizontal_lines'], 'line_prominence': self.completed_key[3],
            'line_coordinate_system': COORDINATE_SYSTEM,
        }
        try:
            save_results(output, self.image, cells, defects, excluded, report)
            save_line_results(output, self.image, metadata['horizontal_lines'])
            self.status.set(f'Gespeichert: {output.name}')
        except (OSError, ValueError, cv2.error) as exception:
            messagebox.showerror('Export fehlgeschlagen', str(exception), parent=self.root)

    def close(self):
        self.is_closed = True
        for job in (self.debounce, self.poll_job, self.resize_job):
            if job is not None:
                self.root.after_cancel(job)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', type=Path, default=Path(__file__).parent / 'Bilder_Processed_1')
    args = parser.parse_args()
    root = tk.Tk()
    try:
        DefectViewer(root, args.folder)
    except (ValueError, OSError) as exception:
        messagebox.showerror('CT-Defektanalyse', str(exception), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == '__main__':
    main()