"""Packaging and dependency-boundary checks without substituting fake QGIS."""
import ast
import configparser
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
from tools.package_plugin import ROOT, build
from transect_navigator import __version__


class ScaffoldTests(unittest.TestCase):
    def test_metadata_and_archive(self):
        metadata = configparser.ConfigParser()
        metadata.read(ROOT / 'transect_navigator/metadata.txt')
        self.assertEqual(metadata['general']['name'], 'Transect Navigator')
        self.assertEqual(metadata['general']['version'], __version__)
        with tempfile.TemporaryDirectory() as folder:
            first = build(Path(folder) / 'one.zip')
            second = build(Path(folder) / 'two.zip')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                self.assertIsNone(archive.testzip())
                names = archive.namelist()
                self.assertIn('transect_navigator/__init__.py', names)
                self.assertIn('transect_navigator/metadata.txt', names)
                for notice in ('README.md', 'CHANGELOG.md', 'LICENSE'):
                    self.assertIn(f'transect_navigator/{notice}', names)
                    self.assertEqual(archive.read(f'transect_navigator/{notice}'),
                                     (ROOT / notice).read_bytes())
                self.assertTrue(all(n.startswith('transect_navigator/') for n in names))
                self.assertFalse(any('__pycache__' in n or n.endswith('.pyc') for n in names))
                for name in names:
                    if name.endswith('.py'):
                        ast.parse(archive.read(name), filename=name)

    def test_core_has_no_qgis_or_ui_imports(self):
        for package in ('geometry', 'navigation', 'qaqc', 'analysis', 'io', 'presentation'):
            for path in (ROOT / 'transect_navigator' / package).glob('*.py'):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [item.name for item in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or '']
                    else:
                        continue
                    for name in names:
                        self.assertFalse(set(name.split('.')) & {'qgis', 'PyQt5', 'PyQt6', 'ui', 'integration'})

    def test_import_is_lazy(self):
        subprocess.run([sys.executable, '-c',
                        'import sys; import transect_navigator; '
                        'assert "qgis" not in sys.modules; '
                        'assert callable(transect_navigator.classFactory)'],
                       cwd=ROOT, check=True)
