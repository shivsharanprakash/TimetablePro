import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests


API_URL = os.environ.get('API_URL', 'http://127.0.0.1:8000')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Timetable Generator')
        self.geometry('1000x700')
        self._build_form()

    def _build_form(self):
        f = ttk.Frame(self)
        f.pack(fill='x', padx=16, pady=12)

        ttk.Label(f, text='Configuration Name').grid(row=0, column=0, sticky='w')
        self.cfg_name = ttk.Entry(f, width=40)
        self.cfg_name.grid(row=0, column=1, sticky='w')

        ttk.Label(f, text='Num Classrooms').grid(row=1, column=0, sticky='w')
        self.rooms = ttk.Entry(f, width=10)
        self.rooms.insert(0, '5')
        self.rooms.grid(row=1, column=1, sticky='w')

        ttk.Label(f, text='Num Labs').grid(row=1, column=2, sticky='w')
        self.labs = ttk.Entry(f, width=10)
        self.labs.insert(0, '3')
        self.labs.grid(row=1, column=3, sticky='w')

        # Simple subject list per year (name + hours) to keep UI lean
        self.sy_subjects = tk.Text(f, width=40, height=4)
        self.ty_subjects = tk.Text(f, width=40, height=4)
        self.bt_subjects = tk.Text(f, width=40, height=4)
        ttk.Label(f, text='SY subjects (name:hours per week; one per line)').grid(row=2, column=0, sticky='w')
        self.sy_subjects.grid(row=2, column=1, columnspan=3, sticky='w')
        ttk.Label(f, text='TY subjects').grid(row=3, column=0, sticky='w')
        self.ty_subjects.grid(row=3, column=1, columnspan=3, sticky='w')
        ttk.Label(f, text='BTech subjects').grid(row=4, column=0, sticky='w')
        self.bt_subjects.grid(row=4, column=1, columnspan=3, sticky='w')

        ttk.Button(f, text='Generate Timetables', command=self._generate).grid(row=5, column=0, pady=10)

        # Tabs for results
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=12, pady=12)

    def _parse_subjects(self, text_widget):
        lines = [l.strip() for l in text_widget.get('1.0', 'end').splitlines() if l.strip()]
        out = []
        for line in lines:
            name, _, hours = line.partition(':')
            try:
                h = int(hours.strip())
            except Exception:
                h = 0
            out.append({'name': name.strip(), 'hours': h, 'labs': 0, 'lab_name': '', 'lab_hours': 0})
        return out

    def _build_payload(self):
        return {
            'config_name': self.cfg_name.get() or 'Config',
            'num_classrooms': int(self.rooms.get() or '0'),
            'num_labs': int(self.labs.get() or '0'),
            'batches': {'SY': 1, 'TY': 1, 'BTech': 1},
            'timings': {'start': '09:15', 'end': '16:15', 'short_break_min': 15, 'lunch_break_min': 45},
            'SY': {'semester': 'odd', 'subjects': self._parse_subjects(self.sy_subjects) or [{'name': 'Math', 'hours': 2, 'labs': 0, 'lab_name': '', 'lab_hours': 0}]},
            'TY': {'semester': 'odd', 'subjects': self._parse_subjects(self.ty_subjects) or [{'name': 'Algo', 'hours': 2, 'labs': 0, 'lab_name': '', 'lab_hours': 0}]},
            'BTech': {'semester': 'even', 'subjects': self._parse_subjects(self.bt_subjects) or [{'name': 'ML', 'hours': 2, 'labs': 0, 'lab_name': '', 'lab_hours': 0}]}
        }

    def _generate(self):
        payload = self._build_payload()
        try:
            r = requests.post(f'{API_URL}/simple/generate', json=payload)
            r.raise_for_status()
        except Exception as e:
            messagebox.showerror('Error', str(e))
            return
        data = r.json()['matrices']
        for tab in self.nb.tabs():
            self.nb.forget(0)
        for year in ['SY', 'TY', 'BTech']:
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=year)
            matrix = data[year]
            cols = [f'C{i}' for i in range(len(matrix[0]))]
            tree = ttk.Treeview(frame, columns=cols, show='headings', height=24)
            for c in cols:
                tree.heading(c, text=c)
                tree.column(c, width=60, anchor='center')
            tree.pack(fill='both', expand=True)
            for row in matrix:
                tree.insert('', 'end', values=[('-' if v is None else v) for v in row])

            def save_csv(y=year):
                try:
                    resp = requests.post(f'{API_URL}/simple/csv', json={'config': payload, 'year': y})
                    resp.raise_for_status()
                    path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile=f'{y}_timetable.csv')
                    if path:
                        with open(path, 'wb') as f:
                            f.write(resp.content)
                except Exception as e:
                    messagebox.showerror('Error', str(e))

            ttk.Button(frame, text=f'Download {year} CSV', command=save_csv).pack(pady=6)


if __name__ == '__main__':
    App().mainloop()


