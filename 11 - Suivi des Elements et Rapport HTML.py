# -*- coding: utf-8 -*-
# ============================================================
# 11 - Suivi des Elements et Rapport HTML (Events-Local-MoMo)
# by Manseur Mohamed
#
# Script Dynamo Python - suivi de l'evolution du modele dans le temps :
# a chaque execution, prend un instantane de tous les elements du projet
# (categorie, famille, type, sous-projet, createur, dernier modificateur,
# parametres) et genere/rafraichit un rapport HTML autonome (double onglet
# Detail/Recapitulatif + diagrammes) permettant de comparer deux dates,
# suivre qui a cree/modifie quoi, et visualiser l'evolution du nombre
# d'elements dans le temps. Aucun dossier a re-choisir a chaque ouverture
# du rapport : tout l'historique est embarque directement dans le HTML.
#
# INPUT : plus de noeud "Directory" a cabler sur le canevas - une fenetre
# demande le dossier d'export au lancement (voir FolderPickerForm
# ci-dessous), avec le dernier dossier utilise propose par defaut.
#
# WHAT THIS DOES EACH RUN:
#   1) Snapshots the CURRENT model state (ElementId, Name, Category, Family,
#      Type, Workset, Creator, LastChangedBy) into its OWN CSV file named
#      by date/time - e.g. Snapshot_2026-09-03_10-15-32.csv. Nothing is
#      appended to one giant file anymore - every run gets its own file.
#   2) Writes/refreshes ElementReport.html and ElementSummary.html. Opening
#      either one needs nothing else: every run's data - every date, every
#      comparison, the Evolution chart, every tracked parameter - is
#      embedded directly in the file and available immediately, no folder
#      to pick. That stays practical as history piles up because the
#      embedded data is dictionary-packed (each column's repeated values -
#      Category, Workset, Creator, ... - stored once per snapshot and
#      referenced by index) and gzip-compressed on top before being
#      embedded as base64 - see dict_encode_snapshot/gzip_b64 and the
#      ReportStore comment near the top of APP_JS_TEMPLATE for the full
#      story, including the two earlier designs this replaced (embedding
#      everything uncompressed grew past ~500MB and stopped opening;
#      embedding only the latest run and asking for a folder pick to see
#      history worked, but meant re-picking a folder every session).
#   3) The report, once loaded, shows ONLY the most recent date by
#      default. A "Compare two dates" panel lets you pick any two
#      snapshot dates and shows ONLY the elements that were Added,
#      Deleted, or Modified between them (with which fields changed, for
#      Modified rows).
#
# Works on both Dynamo Python engines (IronPython2 and CPython3),
# and on Revit 2022 through 2026 (ElementId.Value / .IntegerValue handled
# automatically; no version-specific API is used unguarded).
#
# ---------------------------------------------------------------------
# v2 notes (interface rebuild - data collection logic below is UNCHANGED):
#   - "ElementReport.html" and "ElementSummary.html" are now two tabs of
#     ONE app instead of two separate pages you had to link between.
#     Both files are still written (so old shortcuts/bookmarks to either
#     name keep working) and both are fully standalone/offline, containing
#     the exact same app and data - the only difference is which tab is
#     active by default (Detail for ElementReport.html, Recapitulatif for
#     ElementSummary.html).
#   - The detail table and the summary table used to be two almost-
#     identical copies of the same filter/pagination/export code (~600
#     lines duplicated). They now share one table component, so a fix or
#     an improvement to filtering/paging/export applies to both at once.
#   - Column filter dropdowns used to be trapped inside the header cell,
#     which is exactly why the header could not be sticky before (a
#     comment in v1 explains the clipping bug this caused). The dropdown
#     is now a floating panel positioned from the button's on-screen
#     location, so the header can be sticky (stays visible while you
#     scroll a long table) without breaking the filter menus.
#   - Added one quick-search box per tab that filters across every
#     visible column at once, on top of the existing per-column filters.
# ---------------------------------------------------------------------
# v4 notes (full-history embed, replacing the v3 folder-picker rework -
# see the "WHAT THIS DOES" section above for the current design):
#   - Every run's snapshot is decoded lazily in the browser, one at a
#     time, the first time it's actually asked for (an older date, a
#     comparison, the Evolution chart) - only the latest one is decoded up
#     front, so opening the report costs the same whether the folder holds
#     5 runs or 500 (see ReportStore.getSnapshot in APP_JS_TEMPLATE).
#   - This script re-reads every historical Snapshot_*.csv and
#     ParamsDelta_*.json each run to rebuild the embedded payload (the
#     CSV/JSON files on disk stay the single source of truth) - except for
#     the CURRENT run's own snapshot, which is encoded directly from
#     memory instead of being re-read from the file just written.
#   - Building the payload (dictionary-packing + gzip) is wrapped so that
#     if it ever hits something unanticipated, the report still gets
#     written - just without the embedded data, falling back to its own
#     folder-picker screen - rather than the whole script failing and
#     producing no report at all.
# ---------------------------------------------------------------------

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('RevitNodes')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

import os
import sys
import csv
import json
import re
import codecs
import zlib
import base64
from datetime import datetime

from Autodesk.Revit.DB import (
    FilteredElementCollector, ElementId, BuiltInParameter,
    WorksharingUtils, WorksetId, CategoryType, WorksetKind, StorageType
)
from RevitServices.Persistence import DocumentManager

# Alias (pas de wildcard import) : ce fichier est deja tres charge en noms
# au niveau module (constantes, helpers...) - importer System.Windows.Forms
# / System.Drawing sous alias plutot qu'en "import *" evite tout risque de
# collision silencieuse avec l'un d'eux. Aucun conflit avec Autodesk.Revit.DB
# ici de toute facon : les classes Revit sont importees nommement ci-dessus
# (pas de wildcard), donc pas de symbole "View" en jeu comme dans les autres
# scripts du projet qui ont besoin de l'alias RevitView/RevitTransaction.
import System.Windows.Forms as WinForms
import System.Drawing as WinDrawing

IS_PY2 = sys.version_info[0] < 3

def open_csv_for_write(path):
    if IS_PY2:
        return open(path, mode='wb')
    else:
        return open(path, mode='w', newline='', encoding='utf-8')

def open_csv_for_read(path):
    if IS_PY2:
        return open(path, mode='rb')
    else:
        return open(path, mode='r', newline='', encoding='utf-8')

def to_row_values(values):
    if IS_PY2:
        out = []
        for v in values:
            try:
                out.append(unicode(v))
            except:
                out.append(v)
        return out
    return [str(v) if v is not None else "" for v in values]

doc = DocumentManager.Instance.CurrentDBDocument
is_workshared = doc.IsWorkshared

def get_project_name(document):
    # The Project Information "Project Name" field is what people usually
    # mean, but it's frequently left blank - fall back to the document/
    # central model file title (Document.Title), which is always populated.
    try:
        pi = document.ProjectInformation
        if pi:
            p = pi.get_Parameter(BuiltInParameter.PROJECT_NAME)
            if p and p.HasValue:
                s = p.AsString()
                if s and s.strip():
                    return s.strip()
    except:
        pass
    try:
        t = document.Title
        if t and t.strip():
            return t.strip()
    except:
        pass
    return "N/A"

PROJECT_NAME = get_project_name(doc)

# ---------------------------------------------------------------
# Dossier de sortie - choisi via une interface, comme les autres scripts
# du projet, plutot que par un noeud "Directory" separe a cabler sur le
# canevas Dynamo (ce noeud a ete retire). Le dernier dossier utilise est
# retenu (petit fichier JSON sous %APPDATA%) et propose par defaut au
# prochain lancement.
# ---------------------------------------------------------------
LAST_FOLDER_SETTINGS_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"),
    "BIMATIKA_ElementReport", "last_folder.json")

FALLBACK_FOLDER = os.path.join(os.path.expanduser("~"), "Documents", "BIMATIKA_ElementReport")


def load_last_folder():
    try:
        if os.path.exists(LAST_FOLDER_SETTINGS_PATH):
            with codecs.open(LAST_FOLDER_SETTINGS_PATH, "r", "utf-8") as f:
                data = json.load(f)
            folder = data.get("folder") if isinstance(data, dict) else None
            if folder:
                return folder
    except:
        pass
    return None


def save_last_folder(folder):
    try:
        settings_dir = os.path.dirname(LAST_FOLDER_SETTINGS_PATH)
        if not os.path.exists(settings_dir):
            os.makedirs(settings_dir)
        with codecs.open(LAST_FOLDER_SETTINGS_PATH, "w", "utf-8") as f:
            json.dump({"folder": folder}, f)
    except:
        pass


# NOTE pythonnet (moteur CPython3, ne se produit pas sous IronPython2) :
# Font("Segoe UI", size, FontStyle.X) est ambigu entre les surcharges
# .NET Font(string, float, FontStyle) et Font(string, float, GraphicsUnit)
# - pythonnet peut resoudre vers la MAUVAISE en se basant sur la valeur
# entiere sous-jacente de l'enum, ce qui leve "ArgumentException:
# Parameter is not valid" (System.Drawing.Font.CreateNativeFont) au tout
# premier Font() cree, avant meme l'affichage de la fenetre. Ajouter
# GraphicsUnit.Point comme 4e argument force le seul constructeur a 4
# parametres possible et leve toute ambiguite - Point est de toute facon
# l'unite implicite des surcharges plus courtes, donc aucun changement
# visuel.
class FolderPickerForm(WinForms.Form):
    def __init__(self, default_folder):
        WinForms.Form.__init__(self)
        self.selected_folder = None
        self.default_folder = default_folder
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "11 - Suivi des Elements et Rapport HTML - by Manseur Mohamed"
        self.Size = WinDrawing.Size(560, 250)
        self.StartPosition = WinForms.FormStartPosition.CenterScreen
        self.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = WinDrawing.Color.FromArgb(240, 240, 240)

        lbl_title = WinForms.Label()
        lbl_title.Text = "Suivi des Elements et Rapport HTML"
        lbl_title.Font = WinDrawing.Font("Segoe UI", 14, WinDrawing.FontStyle.Bold, WinDrawing.GraphicsUnit.Point)
        lbl_title.ForeColor = WinDrawing.Color.FromArgb(50, 50, 50)
        lbl_title.Location = WinDrawing.Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        lbl_sig = WinForms.Label()
        lbl_sig.Text = "by Manseur Mohamed"
        lbl_sig.Font = WinDrawing.Font("Segoe UI", 9, WinDrawing.FontStyle.Italic, WinDrawing.GraphicsUnit.Point)
        lbl_sig.ForeColor = WinDrawing.Color.FromArgb(100, 100, 100)
        lbl_sig.Location = WinDrawing.Point(390, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        lbl_subtitle = WinForms.Label()
        lbl_subtitle.Text = "Choisissez le dossier ou exporter les instantanes et le rapport HTML"
        lbl_subtitle.Font = WinDrawing.Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = WinDrawing.Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = WinDrawing.Point(20, 50)
        lbl_subtitle.Size = WinDrawing.Size(510, 20)
        self.Controls.Add(lbl_subtitle)

        lbl_folder = WinForms.Label()
        lbl_folder.Text = "Dossier d'export :"
        lbl_folder.Font = WinDrawing.Font("Segoe UI", 10)
        lbl_folder.Location = WinDrawing.Point(20, 85)
        lbl_folder.AutoSize = True
        self.Controls.Add(lbl_folder)

        self.txt_folder = WinForms.TextBox()
        self.txt_folder.Font = WinDrawing.Font("Segoe UI", 9)
        self.txt_folder.Location = WinDrawing.Point(20, 110)
        self.txt_folder.Size = WinDrawing.Size(400, 25)
        self.txt_folder.Text = self.default_folder or ""
        self.Controls.Add(self.txt_folder)

        self.btn_browse = WinForms.Button()
        self.btn_browse.Text = "Parcourir..."
        self.btn_browse.Font = WinDrawing.Font("Segoe UI", 9)
        self.btn_browse.Location = WinDrawing.Point(430, 108)
        self.btn_browse.Size = WinDrawing.Size(100, 27)
        self.btn_browse.BackColor = WinDrawing.Color.FromArgb(100, 100, 100)
        self.btn_browse.ForeColor = WinDrawing.Color.White
        self.btn_browse.FlatStyle = WinForms.FlatStyle.Flat
        self.btn_browse.FlatAppearance.BorderSize = 0
        self.btn_browse.Click += self.OnBrowse
        self.Controls.Add(self.btn_browse)

        self.btn_ok = WinForms.Button()
        self.btn_ok.Text = "Generer le rapport"
        self.btn_ok.Font = WinDrawing.Font("Segoe UI", 10, WinDrawing.FontStyle.Bold, WinDrawing.GraphicsUnit.Point)
        self.btn_ok.Size = WinDrawing.Size(250, 40)
        self.btn_ok.Location = WinDrawing.Point(20, 160)
        self.btn_ok.BackColor = WinDrawing.Color.FromArgb(0, 120, 215)
        self.btn_ok.ForeColor = WinDrawing.Color.White
        self.btn_ok.FlatStyle = WinForms.FlatStyle.Flat
        self.btn_ok.FlatAppearance.BorderSize = 0
        self.btn_ok.Click += self.OnOk
        self.Controls.Add(self.btn_ok)

        self.btn_cancel = WinForms.Button()
        self.btn_cancel.Text = "Annuler"
        self.btn_cancel.Font = WinDrawing.Font("Segoe UI", 10)
        self.btn_cancel.Size = WinDrawing.Size(250, 40)
        self.btn_cancel.Location = WinDrawing.Point(280, 160)
        self.btn_cancel.BackColor = WinDrawing.Color.FromArgb(100, 100, 100)
        self.btn_cancel.ForeColor = WinDrawing.Color.White
        self.btn_cancel.FlatStyle = WinForms.FlatStyle.Flat
        self.btn_cancel.FlatAppearance.BorderSize = 0
        self.btn_cancel.Click += self.OnCancel
        self.Controls.Add(self.btn_cancel)

    def OnBrowse(self, sender, event):
        dlg = WinForms.FolderBrowserDialog()
        dlg.Description = "Choisissez le dossier d'export du rapport"
        try:
            current = self.txt_folder.Text.strip()
            if current and os.path.isdir(current):
                dlg.SelectedPath = current
        except:
            pass
        if dlg.ShowDialog() == WinForms.DialogResult.OK:
            self.txt_folder.Text = dlg.SelectedPath

    def OnOk(self, sender, event):
        folder = self.txt_folder.Text.strip()
        if not folder:
            WinForms.MessageBox.Show("Veuillez choisir un dossier.", "Attention",
                                      WinForms.MessageBoxButtons.OK, WinForms.MessageBoxIcon.Warning)
            return
        self.selected_folder = folder
        self.DialogResult = WinForms.DialogResult.OK
        self.Close()

    def OnCancel(self, sender, event):
        self.DialogResult = WinForms.DialogResult.Cancel
        self.Close()


# NOTE : en cas d'Annuler, le script continue quand meme avec le dernier
# dossier connu (ou le dossier par defaut) plutot que de s'arreter - ce
# script n'est pas structure en fonction main() comme les autres scripts
# du projet (c'est un long script imperatif de haut niveau, herite de ses
# versions precedentes v1-v4), et l'interrompre proprement en plein milieu
# demanderait de restructurer l'ensemble en profondeur. Annuler revient
# donc a "generer quand meme, avec le dossier par defaut" plutot qu'a
# "ne rien generer du tout".
_default_export_folder = load_last_folder() or FALLBACK_FOLDER
_folder_form = FolderPickerForm(_default_export_folder)
if _folder_form.ShowDialog() == WinForms.DialogResult.OK:
    output_folder = _folder_form.selected_folder
else:
    output_folder = _default_export_folder

save_last_folder(output_folder)

if not os.path.exists(output_folder):
    os.makedirs(output_folder)

HTML_OUTPUT_PATH = os.path.join(output_folder, "ElementReport.html")

now = datetime.now()
run_label_file = now.strftime("%Y-%m-%d_%H-%M-%S")   # filename-safe
run_label_display = now.strftime("%Y-%m-%d %H:%M:%S")  # human/sortable

SNAPSHOT_PREFIX = "Snapshot_"
SNAPSHOT_SUFFIX = ".csv"
THIS_SNAPSHOT_PATH = os.path.join(output_folder, SNAPSHOT_PREFIX + run_label_file + SNAPSHOT_SUFFIX)

# ---------------------------------------------------------------
# Collect elements - current state
# ---------------------------------------------------------------
# NOTE: we do NOT use .WhereElementIsViewIndependent() because it would
# exclude ALL annotation elements (text, dimensions, tags...) which we
# specifically want to count. We also avoid collector.ToElements(), which
# can raise an InternalException in worksharing when a single element fails
# to regenerate. Instead we pull element ids and fetch each one defensively,
# so one bad element can never abort the whole collection.
collector = FilteredElementCollector(doc).WhereElementIsNotElementType()

element_ids = []
try:
    element_ids = list(collector.ToElementIds())
except:
    try:
        # Fallback: lazy enumeration if ToElementIds also fails
        for _e in collector:
            try:
                element_ids.append(_e.Id)
            except:
                pass
    except:
        element_ids = []

elements = []
for _eid in element_ids:
    try:
        _el = doc.GetElement(_eid)
        if _el is not None:
            elements.append(_el)
    except:
        pass

def get_id_value(element_id):
    # Revit 2024+ replaced ElementId.IntegerValue with ElementId.Value
    # (a 64-bit long); Revit 2026 removed IntegerValue entirely. Support both.
    try:
        return element_id.Value
    except:
        pass
    try:
        return element_id.IntegerValue
    except:
        return -1

def get_element_name(el):
    try:
        return el.Name
    except:
        return "N/A"

def get_category_name(el):
    try:
        return el.Category.Name if el.Category else "<No Category>"
    except:
        return "<No Category>"

def get_category_type(el):
    # Return a clear text label ("Model" / "Annotation" / ...), NOT the enum's
    # numeric value. On some Python engines str(CategoryType) yields the number
    # (2, 3...) instead of the name, so we compare against the enum explicitly.
    try:
        if el.Category:
            ct = el.Category.CategoryType
            if ct == CategoryType.Model:
                return "Model"
            if ct == CategoryType.Annotation:
                return "Annotation"
            if ct == CategoryType.Internal:
                return "Internal"
            # AnalyticalModel exists only in some API versions - guard it.
            try:
                if ct == CategoryType.AnalyticalModel:
                    return "AnalyticalModel"
            except:
                pass
            # Last resort: .ToString() on a .NET enum returns the member name.
            try:
                return ct.ToString()
            except:
                return "Autre"
    except:
        pass
    return "N/A"

def get_family_and_type(el, document):
    # Each lookup is isolated so that a failure on one (e.g. .Name throwing
    # on some types in Revit 2026/CPython) does NOT wipe out the other -
    # which was making both Type and Family come back as "N/A".
    fam_name = "N/A"
    type_name = "N/A"
    elem_type = None
    try:
        type_id = el.GetTypeId()
        if type_id is not None and type_id != ElementId.InvalidElementId:
            elem_type = document.GetElement(type_id)
    except:
        elem_type = None

    if elem_type is not None:
        # --- Type name: try .Name, then type-name parameters ---
        try:
            n = elem_type.Name
            if n:
                type_name = n
        except:
            pass
        if type_name == "N/A":
            for bip in (BuiltInParameter.SYMBOL_NAME_PARAM,
                        BuiltInParameter.ALL_MODEL_TYPE_NAME):
                try:
                    p = elem_type.get_Parameter(bip)
                    if p and p.HasValue:
                        s = p.AsString()
                        if s:
                            type_name = s
                            break
                except:
                    pass

        # --- Family name: FamilyName property, then parameter, then .Family ---
        try:
            fn = elem_type.FamilyName   # ElementType.FamilyName (works for system + loadable)
            if fn:
                fam_name = fn
        except:
            pass
        if fam_name == "N/A":
            try:
                p = elem_type.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                if p and p.HasValue:
                    s = p.AsString()
                    if s:
                        fam_name = s
            except:
                pass
        if fam_name == "N/A":
            try:
                if hasattr(elem_type, "Family") and elem_type.Family:
                    fam_name = elem_type.Family.Name
            except:
                pass

    return fam_name, type_name

def get_workset_info(el, document, workshared):
    # Returns (workset_name, workset_kind_label). The kind distinguishes
    # user / family / view / project(other) sub-projects for filtering.
    if not workshared:
        return "N/A", "N/A"
    try:
        ws_param = el.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if ws_param and ws_param.HasValue:
            ws_id = ws_param.AsInteger()
            workset = document.GetWorksetTable().GetWorkset(WorksetId(ws_id))
            if workset:
                name = workset.Name
                kind = "N/A"
                try:
                    k = workset.Kind
                    if k == WorksetKind.UserWorkset:
                        kind = "Utilisateur"
                    elif k == WorksetKind.FamilyWorkset:
                        kind = "Familles"
                    elif k == WorksetKind.ViewWorkset:
                        kind = "Vues"
                    elif k == WorksetKind.OtherWorkset:
                        kind = "Projet/Autre"
                    else:
                        try:
                            kind = k.ToString()
                        except:
                            kind = "Autre"
                except:
                    pass
                return name, kind
    except:
        pass
    return "N/A", "N/A"

def get_worksharing_info(el, document, workshared):
    if not workshared:
        return "N/A", "N/A"
    try:
        info = WorksharingUtils.GetWorksharingTooltipInfo(document, el.Id)
        creator = info.Creator if info.Creator else "N/A"
        last_changed_by = info.LastChangedBy if info.LastChangedBy else "N/A"
        return creator, last_changed_by
    except:
        return "N/A", "N/A"

def get_all_parameters(el, document):
    # Every parameter on the element, as {name: display-string-value},
    # skipping anything with no real value. AsValueString() is tried first
    # because it's unit-aware and human-readable (e.g. "10' - 0\"" instead
    # of a raw internal double); each fallback is its own try/except so one
    # broken parameter can never take the rest of the element's parameters
    # down with it - same defensive shape as every other get_* helper here.
    result = {}
    try:
        params = el.Parameters
    except:
        return result
    for p in params:
        try:
            defn = p.Definition
            name = defn.Name if defn else None
            if not name:
                continue
            val = None
            try:
                val = p.AsValueString()
            except:
                val = None
            if not val:
                try:
                    st = p.StorageType
                except:
                    st = None
                try:
                    if st == StorageType.String:
                        val = p.AsString()
                    elif st == StorageType.Double:
                        v = p.AsDouble()
                        val = None if v is None else str(v)
                    elif st == StorageType.Integer:
                        v = p.AsInteger()
                        val = None if v is None else str(v)
                    elif st == StorageType.ElementId:
                        eid = p.AsElementId()
                        if eid is not None and eid != ElementId.InvalidElementId:
                            other = document.GetElement(eid)
                            val = get_element_name(other) if other is not None else str(get_id_value(eid))
                except:
                    val = None
            if not val:
                continue
            val = val.strip() if hasattr(val, "strip") else str(val)
            if val:
                result[name] = val
        except:
            pass
    return result

def load_json_safe(path, default):
    try:
        if os.path.exists(path):
            with codecs.open(path, "r", "utf-8") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json_safe(path, obj):
    try:
        with codecs.open(path, "w", "utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except:
        pass

SNAPSHOT_HEADER = ["ElementId", "Name", "Category", "CategoryType", "Family", "Type", "Workset", "TypeSousProjet", "Creator", "LastChangedBy"]

current_run_rows = []
# Every parameter beyond the fixed 10 columns above, keyed by ElementId (as
# a string, to match how it's used as a JSON object key everywhere below).
# The Detail tab's "Add parameters" section is what actually surfaces this -
# see the v2 notes near ParamsDelta_ below for why this is stored as a
# change-log instead of a full table on every run.
current_run_params = {}
for el in elements:
    eid_val = get_id_value(el.Id)
    try:
        name = get_element_name(el)
        cat = get_category_name(el)
        cat_type = get_category_type(el)
        fam, typ = get_family_and_type(el, doc)
        ws, ws_kind = get_workset_info(el, doc, is_workshared)
        creator, last_changed = get_worksharing_info(el, doc, is_workshared)
        current_run_rows.append([eid_val, name, cat, cat_type, fam, typ, ws, ws_kind, creator, last_changed])
        current_run_params[str(eid_val)] = get_all_parameters(el, doc)
    except Exception as e:
        current_run_rows.append([eid_val, "ERROR", str(e), "", "", "", "", "", "", ""])

# ---------------------------------------------------------------
# Write THIS run to its own snapshot CSV (does not touch other files)
# ---------------------------------------------------------------
with open_csv_for_write(THIS_SNAPSHOT_PATH) as f:
    writer = csv.writer(f)
    writer.writerow(to_row_values(SNAPSHOT_HEADER))
    for row in current_run_rows:
        writer.writerow(to_row_values(row))

# ---------------------------------------------------------------
# Full parameter set per element - written as a CHANGE LOG, not a full
# table every run. Every element can carry 50-150+ parameters, so writing
# all of them for every element on every run would make each export huge
# even when almost nothing changed since the last one. Instead:
#   - "_params_baseline.json" holds the last known value of every
#     parameter this script has ever seen, per element - it's read at the
#     start of this block and overwritten at the end with this run's
#     values merged in.
#   - Only values that are NEW or DIFFERENT from that baseline get written
#     to this run's own "ParamsDelta_<timestamp>.json" (skipped entirely
#     if nothing changed). The very first run is the one exception: with
#     no baseline yet, everything is "new" - that's expected, not a bug.
#   - The HTML report embeds every ParamsDelta_*.json it finds and replays
#     them client-side to answer "what was parameter P on element E as of
#     date D" - see setupDetailTab's getResolvedParamsAt().
# ---------------------------------------------------------------
PARAMS_BASELINE_PATH = os.path.join(output_folder, "_params_baseline.json")
PARAMS_DELTA_PREFIX = "ParamsDelta_"

baseline_params = load_json_safe(PARAMS_BASELINE_PATH, {})
if not isinstance(baseline_params, dict):
    baseline_params = {}

params_delta = {}
for eid_str, pmap in current_run_params.items():
    base_pmap = baseline_params.get(eid_str, {})
    changed = {}
    for pname, pval in pmap.items():
        if base_pmap.get(pname) != pval:
            changed[pname] = pval
    if changed:
        params_delta[eid_str] = changed

if params_delta:
    delta_path = os.path.join(output_folder, PARAMS_DELTA_PREFIX + run_label_file + ".json")
    save_json_safe(delta_path, {"date": run_label_display, "changes": params_delta})

for eid_str, pmap in current_run_params.items():
    if eid_str not in baseline_params:
        baseline_params[eid_str] = {}
    baseline_params[eid_str].update(pmap)
save_json_safe(PARAMS_BASELINE_PATH, baseline_params)

# ---------------------------------------------------------------
# Build the embedded payload: EVERY run's snapshot (dictionary-packed -
# see dict_encode_snapshot) + every parameter delta, gzip-compressed. This
# is what lets the report show full history with nothing to pick, while
# staying small: repeated column values (Category, Workset, Creator, ...)
# are stored once and referenced by index, then gzip removes most of what
# the dictionary-packing didn't already catch. See ReportStore near the
# top of APP_JS_TEMPLATE for the browser-side half of this.
# ---------------------------------------------------------------
def dict_encode_snapshot(columns, rows):
    n = len(columns)
    dicts = [[] for _ in range(n)]
    idx_maps = [dict() for _ in range(n)]
    enc_rows = []
    for row in rows:
        er = []
        for ci in range(n):
            v = row[ci] if ci < len(row) else ""
            im = idx_maps[ci]
            if v in im:
                idx = im[v]
            else:
                idx = len(dicts[ci])
                dicts[ci].append(v)
                im[v] = idx
            er.append(idx)
        enc_rows.append(er)
    return {"columns": list(columns), "dict": dicts, "rows": enc_rows}

def gzip_b64(text):
    # wbits=31 asks zlib for gzip framing (not raw deflate/zlib) - the
    # format the browser's native DecompressionStream('gzip') expects.
    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    packed = co.compress(text.encode("utf-8")) + co.flush()
    encoded = base64.b64encode(packed)
    # Py2's b64encode returns a plain str already; Py3's returns bytes.
    return encoded if IS_PY2 else encoded.decode("ascii")

filename_pattern = re.compile(r'^Snapshot_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.csv$')
snapshots_encoded = {}
for fname in os.listdir(output_folder):
    match = filename_pattern.match(fname)
    if not match:
        continue
    file_label = match.group(1)
    try:
        dt = datetime.strptime(file_label, "%Y-%m-%d_%H-%M-%S")
        display_label = dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        display_label = file_label

    if file_label == run_label_file:
        # This run's own snapshot - already in memory (and just written to
        # this exact file), so encode it directly instead of re-reading and
        # re-parsing the CSV we only just wrote a moment ago.
        snapshots_encoded[display_label] = dict_encode_snapshot(SNAPSHOT_HEADER, [to_row_values(r) for r in current_run_rows])
        continue

    fpath = os.path.join(output_folder, fname)
    try:
        with open_csv_for_read(fpath) as f:
            reader = csv.reader(f)
            rows = list(reader)
            if not rows:
                continue
            file_header = rows[0]
            file_rows = [r for r in rows[1:] if len(r) == len(file_header)]
            snapshots_encoded[display_label] = dict_encode_snapshot(file_header, file_rows)
    except Exception:
        pass

if not snapshots_encoded:
    snapshots_encoded[run_label_display] = dict_encode_snapshot(SNAPSHOT_HEADER, [to_row_values(r) for r in current_run_rows])

total_snapshots = len(snapshots_encoded)
latest_label = sorted(snapshots_encoded.keys())[-1]

all_deltas = []
paramdelta_pattern = re.compile(r'^ParamsDelta_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.json$')
for fname in os.listdir(output_folder):
    if not paramdelta_pattern.match(fname):
        continue
    data = load_json_safe(os.path.join(output_folder, fname), None)
    if isinstance(data, dict) and "changes" in data:
        all_deltas.append({"date": data.get("date", fname), "changes": data["changes"]})
all_deltas.sort(key=lambda d: d["date"])

available_params_set = set()
for _eid_str, _pmap in baseline_params.items():
    for _pname in _pmap.keys():
        available_params_set.add(_pname)
available_params = sorted(available_params_set)

def html_escape(s):
    # Plain-text-into-HTML escaping for values placed directly into the
    # page markup (project name, run date). A project name can contain
    # &, <, >, or " - none of that should reach the page unescaped.
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


PROJECT_NAME_HTML = html_escape(PROJECT_NAME)

# ---------------------------------------------------------------
# Build the HTML report: the browser reads Snapshot_*.csv / ParamsDelta_*.
# json / _params_baseline.json straight from the export folder once the
# report is pointed at it (see ReportStore at the top of APP_JS_TEMPLATE
# below) - no data payload is assembled or embedded here any more, which
# is what keeps this HTML a fixed, small size regardless of how many runs
# the folder holds.
# ---------------------------------------------------------------

# =====================================================================
# APP SHELL - one shared CSS block + one shared JS table component used
# by both the Detail tab and the Summary tab. See the v2 notes at the
# top of this file for why this replaced two separate HTML builders.
# =====================================================================

APP_CSS = """
:root{
  /* Palette matches BIMATIKA/BimFlow's own product UI (bimatika-bimplan.pages.dev) -
     same accent (sky-600/sky-400), same active-nav accent-soft pairing,
     same card radius - so this report feels like part of the same family
     of tools instead of a generic gray dashboard. */
  --bg:#eef1f6; --panel:#ffffff; --border:#dde2ec; --border-soft:#e8ecf3;
  --text:#1e2733; --text-2:#475569; --text-3:#64748b;
  --accent:#0284c7; --accent-2:#0369a1; --accent-soft:#e7f3fc;
  --added:#1a9d4c; --added-bg:#dcf6e4; --added-line:#1a9d4c;
  --deleted:#c0202f; --deleted-bg:#fbe0e1; --deleted-line:#c0202f;
  --modified:#a3720a; --modified-bg:#fdefcf; --modified-line:#c98a12;
  --radius:8px; --radius-lg:14px; --mono:Consolas,'Courier New',monospace;
  --shadow:0 2px 10px rgba(15,35,60,0.08);
  --cta-grad:linear-gradient(135deg,#1d4ed8,#0ea5e9);
  --cta-grad-hover:linear-gradient(135deg,#1743b8,#0b8ec7);
  /* Chart categorical palette - fixed hue order (identity, never cycled by
     rank). Validated set: worst adjacent CVD Delta E 9.1, worst adjacent
     normal-vision Delta E 19.6 (both >= the 8/15 targets). Kept separate
     from --accent (that's UI chrome, not series identity) and unchanged
     by the BimFlow-style reskin below - only chrome colors moved. */
  --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#eda100;
  --series-5:#e87ba4; --series-6:#008300; --series-7:#4a3aa7; --series-8:#e34948;
}
body.dark{
  --bg:#0a1120; --panel:#111827; --border:#1f2937; --border-soft:#172236;
  --text:#e2e8f0; --text-2:#94a3b8; --text-3:#64748b;
  --accent:#38bdf8; --accent-2:#0ea5e9; --accent-soft:#0c1a2e;
  --added:#3fcf75; --added-bg:#123a22; --added-line:#3fcf75;
  --deleted:#f0666a; --deleted-bg:#3c1719; --deleted-line:#f0666a;
  --modified:#f0b429; --modified-bg:#3a2c07; --modified-line:#f0b429;
  --shadow:0 2px 14px rgba(0,0,0,0.5);
  /* Same eight hues, stepped for the dark surface - not a separate palette. */
  --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#c98500;
  --series-5:#d55181; --series-6:#008300; --series-7:#9085e9; --series-8:#e66767;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13.5px;line-height:1.4;}
button,select,input{font-family:inherit;font-size:12.5px;}
button{
  background:var(--panel);color:var(--text);border:1px solid var(--border);
  border-radius:var(--radius);padding:6px 12px;cursor:pointer;
}
button:hover{border-color:var(--accent);color:var(--accent);}
button:active{transform:translateY(1px);}
button.primary{background:var(--cta-grad);color:#fff;border-color:transparent;font-weight:600;}
button.primary:hover{background:var(--cta-grad-hover);border-color:transparent;color:#fff;}
select,input[type=text]{
  background:var(--panel);color:var(--text);border:1px solid var(--border);
  border-radius:var(--radius);padding:5px 8px;
}
a{color:var(--accent);}

/* ---- App shell / tabs ---- */
#appHeader{
  position:sticky;top:0;z-index:30;background:var(--panel);
  border-bottom:1px solid var(--border);box-shadow:var(--shadow);
}
#appHeader .inner{display:flex;align-items:center;gap:18px;padding:10px 16px;flex-wrap:wrap;}
#appTitle{font-weight:700;font-size:15px;white-space:nowrap;}
#appTitle .path{font-weight:400;color:var(--text-3);font-family:var(--mono);font-size:11.5px;}
#appTitle .projname{color:var(--accent);font-weight:800;}
/* Hidden until a report folder is picked and its latest snapshot is
   loaded (see the onboarding wiring at the bottom of report_app.js) -
   avoids showing tabs whose content hasn't been set up yet. */
.tabbar{display:none;gap:2px;}
body.bimreport-ready .tabbar{display:flex;}
/* Flat nav-link + bottom-highlight, same treatment BimFlow itself uses for
   its own top nav (accent-soft background + accent text + accent underline
   on the active item) rather than a filled pill. */
.tabbtn{
  border:1px solid transparent;background:transparent;color:var(--text-2);
  border-radius:var(--radius);padding:8px 16px;cursor:pointer;font-weight:600;
}
.tabbtn:hover{background:var(--border-soft);color:var(--text);}
.tabbtn.active{background:var(--accent-soft);color:var(--accent);box-shadow:inset 0 -2px 0 var(--accent);}
.appspacer{flex:1;}
.quicksearch{position:relative;}
.quicksearch input{width:220px;padding:6px 10px 6px 28px;}
.quicksearch:before{content:"\\1F50D";position:absolute;left:8px;top:50%;transform:translateY(-50%);font-size:11px;color:var(--text-3);}

.wrap{max-width:1400px;margin:0 auto;padding:16px;}
.tabpanel{display:none;}
.tabpanel.active{display:block;}

/* ---- Onboarding gate - shown until the report folder is picked ---- */
#loadGate{max-width:640px;margin:40px auto;}
#loadGate p{margin:0 0 10px;}
#loadGate code{font-family:var(--mono);background:var(--bg);border-radius:4px;padding:1px 5px;font-size:11.5px;}
#loadGate .hint{display:block;margin-top:10px;}
#loadGate .hint.gate-error{color:var(--deleted);font-weight:600;}
body.bimreport-ready #loadGate{display:none;}

.panel{
  background:var(--panel);border:1px solid var(--border-soft);border-radius:var(--radius-lg);
  padding:14px 16px;margin-bottom:14px;box-shadow:var(--shadow);
}
.panel h2{font-size:13px;margin:0 0 10px;color:var(--text-2);text-transform:uppercase;letter-spacing:.04em;}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px;}
.row:last-child{margin-bottom:0;}
.hint{color:var(--text-3);font-size:11.5px;}
.legend span{display:inline-block;width:13px;height:13px;margin-right:4px;vertical-align:middle;border-radius:3px;border:1px solid rgba(0,0,0,.15);}

.subtitle{color:var(--text-2);font-size:12px;margin:2px 0 10px;}

/* ---- Grouping list (Summary tab) ---- */
.gc-row{display:flex;align-items:center;gap:6px;padding:2px 0;}
.gc-row button{padding:1px 7px;font-size:11px;}
.gc-row span.lbl{min-width:130px;}

/* ---- Table ---- */
.table-toolbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:8px;font-size:12.5px;color:var(--text-2);}
.table-toolbar .grow{flex:1;}
.table-scroll{
  max-height:62vh;overflow:auto;border:1px solid var(--border);border-radius:var(--radius-lg);
  background:var(--panel);
}
/* table-layout:fixed + a <colgroup> the JS controls, WITHOUT width:100% -
   a fixed layout that's also forced to 100% divides that space evenly
   across every column (what used to squeeze headers into illegible
   overlapping text once Compare-two-dates' extra columns pushed the count
   past ~10). Fixed layout without width:100% instead sizes columns from
   their <col> widths alone, and .table-scroll's own overflow:auto adds a
   horizontal scrollbar once the total exceeds the panel - so a column can
   be dragged narrower AND text wraps instead of vanishing (see
   .col-resize-handle below), while nothing forces the whole table to
   cram itself into the visible width. */
table{border-collapse:collapse;font-size:12.5px;table-layout:fixed;}
td{border-bottom:1px solid var(--border-soft);border-right:1px solid var(--border-soft);padding:6px 10px;text-align:left;white-space:normal;word-break:break-word;}
td.count{text-align:right;font-variant-numeric:tabular-nums;}
th{
  border-bottom:1px solid var(--border);border-right:1px solid var(--border-soft);
  padding:7px 10px;text-align:left;white-space:normal;word-break:break-word;background:var(--accent-soft);color:var(--accent);
  user-select:none;position:sticky;top:0;z-index:2;font-weight:700;
}
.col-resize-handle{position:absolute;top:0;right:0;bottom:0;width:6px;cursor:col-resize;z-index:3;}
.col-resize-handle:hover,.col-resize-handle.active{background:var(--accent);opacity:.5;}
tbody tr:nth-child(even) td{background:var(--border-soft);}
tbody tr:hover td{background:rgba(2,132,199,0.08);}
body.dark tbody tr:hover td{background:rgba(56,189,248,0.10);}

tr.diff-added td{background:var(--added-bg);}
tr.diff-added td:first-child{border-left:4px solid var(--added-line);}
tr.diff-deleted td{background:var(--deleted-bg);}
tr.diff-deleted td:first-child{border-left:4px solid var(--deleted-line);}
tr.diff-modified td{background:var(--modified-bg);}
tr.diff-modified td:first-child{border-left:4px solid var(--modified-line);}

.badge{display:inline-block;padding:2px 9px;border-radius:10px;color:#fff;font-weight:700;font-size:11px;}
.badge-added{background:var(--added);}
.badge-deleted{background:var(--deleted);}
.badge-modified{background:var(--modified);}

.filter-btn{margin-left:6px;cursor:pointer;font-weight:normal;opacity:.7;}
.filter-btn.active{opacity:1;color:#22d3ee;}

/* Floating filter panel: positioned via JS (top/left), NOT nested inside
   the header cell - this is what lets the header be sticky (see v2 notes). */
#filterFloat{
  position:fixed;display:none;background:var(--panel);color:var(--text);border:1px solid var(--border);
  padding:8px;z-index:1000;max-height:320px;overflow-y:auto;min-width:210px;
  box-shadow:var(--shadow);font-weight:normal;font-size:12.5px;border-radius:var(--radius-lg);
}
#filterFloat label{display:block;font-weight:normal;white-space:nowrap;padding:1px 0;}
#filterFloat input[type=text]{width:100%;box-sizing:border-box;margin-bottom:6px;}
#filterFloat .ftbtns{margin-top:6px;display:flex;gap:6px;}
#filterFloat .filter-note{color:var(--text-3);font-style:italic;font-size:11px;margin-top:4px;}

#pager{display:flex;align-items:center;gap:8px;}
#pageInfo{min-width:110px;text-align:center;}

/* ---- Detail tab: extra-parameter chips ---- */
.chip{display:inline-flex;align-items:center;gap:4px;background:var(--border-soft);border:1px solid var(--border);border-radius:999px;padding:3px 6px 3px 10px;font-size:11.5px;color:var(--text-2);margin:2px 4px 2px 0;}
.chip-x{background:none;border:none;color:var(--text-3);cursor:pointer;font-size:14px;line-height:1;padding:0 2px;}
.chip-x:hover{color:var(--deleted);}

/* ---- Diagrams tab ---- */
.viz-layout{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
@media(max-width:880px){.viz-layout{grid-template-columns:1fr;}}
.viz-card-wide{grid-column:1/-1;}
.viz-svg{width:100%;height:auto;display:block;overflow:visible;}
.viz-svg text{font-family:inherit;}
.viz-catlabel{font-size:11px;fill:var(--text-2);}
.viz-vallabel{font-size:11px;fill:var(--text-2);font-variant-numeric:tabular-nums;}
.viz-axislabel{font-size:10px;fill:var(--text-3);}
.viz-gridline{stroke:var(--border-soft);stroke-width:1;}
.viz-empty{color:var(--text-3);font-size:13px;padding:20px 4px;line-height:1.5;}
.viz-donut-wrap{display:flex;align-items:center;gap:22px;flex-wrap:wrap;}
.viz-donut-wrap>div:first-child{max-width:200px;flex-shrink:0;width:100%;}
.viz-legend{display:flex;flex-direction:column;gap:7px;font-size:12.5px;color:var(--text-2);}
.viz-swatch{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:7px;vertical-align:middle;}
.viz-legend-row{flex-direction:row;flex-wrap:wrap;gap:6px 16px;}
.viz-card-head{display:flex;justify-content:space-between;align-items:center;gap:8px;}
.viz-remove{background:none;border:none;color:var(--text-3);font-size:19px;line-height:1;cursor:pointer;padding:0 2px;}
.viz-remove:hover{color:var(--deleted);border-color:transparent;}
#viz_trendSliderRow input[type=range]{width:180px;vertical-align:middle;}
</style>""".strip()


APP_JS_TEMPLATE = r"""
<script>
// pako (inflate-only build, v2.1.0, MIT/Zlib) - https://github.com/nodeca/pako
// Vendored so gzip decompression works even in a browser without the native
// DecompressionStream API (used as a fallback - see decompressPayload()
// below). Defines the global `pako`. Unmodified except for this note.
!function(e,t){"object"==typeof exports&&"undefined"!=typeof module?t(exports):"function"==typeof define&&define.amd?define(["exports"],t):t((e="undefined"!=typeof globalThis?globalThis:e||self).pako={})}(this,(function(e){"use strict";var t=(e,t,i,n)=>{let a=65535&e|0,r=e>>>16&65535|0,o=0;for(;0!==i;){o=i>2e3?2e3:i,i-=o;do{a=a+t[n++]|0,r=r+a|0}while(--o);a%=65521,r%=65521}return a|r<<16|0};const i=new Uint32Array((()=>{let e,t=[];for(var i=0;i<256;i++){e=i;for(var n=0;n<8;n++)e=1&e?3988292384^e>>>1:e>>>1;t[i]=e}return t})());var n=(e,t,n,a)=>{const r=i,o=a+n;e^=-1;for(let i=a;i<o;i++)e=e>>>8^r[255&(e^t[i])];return-1^e};const a=16209;var r=function(e,t){let i,n,r,o,s,l,d,f,c,h,u,w,b,m,k,_,g,p,v,x,y,E,R,A;const Z=e.state;i=e.next_in,R=e.input,n=i+(e.avail_in-5),r=e.next_out,A=e.output,o=r-(t-e.avail_out),s=r+(e.avail_out-257),l=Z.dmax,d=Z.wsize,f=Z.whave,c=Z.wnext,h=Z.window,u=Z.hold,w=Z.bits,b=Z.lencode,m=Z.distcode,k=(1<<Z.lenbits)-1,_=(1<<Z.distbits)-1;e:do{w<15&&(u+=R[i++]<<w,w+=8,u+=R[i++]<<w,w+=8),g=b[u&k];t:for(;;){if(p=g>>>24,u>>>=p,w-=p,p=g>>>16&255,0===p)A[r++]=65535&g;else{if(!(16&p)){if(0==(64&p)){g=b[(65535&g)+(u&(1<<p)-1)];continue t}if(32&p){Z.mode=16191;break e}e.msg="invalid literal/length code",Z.mode=a;break e}v=65535&g,p&=15,p&&(w<p&&(u+=R[i++]<<w,w+=8),v+=u&(1<<p)-1,u>>>=p,w-=p),w<15&&(u+=R[i++]<<w,w+=8,u+=R[i++]<<w,w+=8),g=m[u&_];i:for(;;){if(p=g>>>24,u>>>=p,w-=p,p=g>>>16&255,!(16&p)){if(0==(64&p)){g=m[(65535&g)+(u&(1<<p)-1)];continue i}e.msg="invalid distance code",Z.mode=a;break e}if(x=65535&g,p&=15,w<p&&(u+=R[i++]<<w,w+=8,w<p&&(u+=R[i++]<<w,w+=8)),x+=u&(1<<p)-1,x>l){e.msg="invalid distance too far back",Z.mode=a;break e}if(u>>>=p,w-=p,p=r-o,x>p){if(p=x-p,p>f&&Z.sane){e.msg="invalid distance too far back",Z.mode=a;break e}if(y=0,E=h,0===c){if(y+=d-p,p<v){v-=p;do{A[r++]=h[y++]}while(--p);y=r-x,E=A}}else if(c<p){if(y+=d+c-p,p-=c,p<v){v-=p;do{A[r++]=h[y++]}while(--p);if(y=0,c<v){p=c,v-=p;do{A[r++]=h[y++]}while(--p);y=r-x,E=A}}}else if(y+=c-p,p<v){v-=p;do{A[r++]=h[y++]}while(--p);y=r-x,E=A}for(;v>2;)A[r++]=E[y++],A[r++]=E[y++],A[r++]=E[y++],v-=3;v&&(A[r++]=E[y++],v>1&&(A[r++]=E[y++]))}else{y=r-x;do{A[r++]=A[y++],A[r++]=A[y++],A[r++]=A[y++],v-=3}while(v>2);v&&(A[r++]=A[y++],v>1&&(A[r++]=A[y++]))}break}}break}}while(i<n&&r<s);v=w>>3,i-=v,w-=v<<3,u&=(1<<w)-1,e.next_in=i,e.next_out=r,e.avail_in=i<n?n-i+5:5-(i-n),e.avail_out=r<s?s-r+257:257-(r-s),Z.hold=u,Z.bits=w};const o=15,s=new Uint16Array([3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258,0,0]),l=new Uint8Array([16,16,16,16,16,16,16,16,17,17,17,17,18,18,18,18,19,19,19,19,20,20,20,20,21,21,21,21,16,72,78]),d=new Uint16Array([1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577,0,0]),f=new Uint8Array([16,16,16,16,17,17,18,18,19,19,20,20,21,21,22,22,23,23,24,24,25,25,26,26,27,27,28,28,29,29,64,64]);var c=(e,t,i,n,a,r,c,h)=>{const u=h.bits;let w,b,m,k,_,g,p=0,v=0,x=0,y=0,E=0,R=0,A=0,Z=0,S=0,T=0,O=null;const U=new Uint16Array(16),D=new Uint16Array(16);let I,B,N,C=null;for(p=0;p<=o;p++)U[p]=0;for(v=0;v<n;v++)U[t[i+v]]++;for(E=u,y=o;y>=1&&0===U[y];y--);if(E>y&&(E=y),0===y)return a[r++]=20971520,a[r++]=20971520,h.bits=1,0;for(x=1;x<y&&0===U[x];x++);for(E<x&&(E=x),Z=1,p=1;p<=o;p++)if(Z<<=1,Z-=U[p],Z<0)return-1;if(Z>0&&(0===e||1!==y))return-1;for(D[1]=0,p=1;p<o;p++)D[p+1]=D[p]+U[p];for(v=0;v<n;v++)0!==t[i+v]&&(c[D[t[i+v]]++]=v);if(0===e?(O=C=c,g=20):1===e?(O=s,C=l,g=257):(O=d,C=f,g=0),T=0,v=0,p=x,_=r,R=E,A=0,m=-1,S=1<<E,k=S-1,1===e&&S>852||2===e&&S>592)return 1;for(;;){I=p-A,c[v]+1<g?(B=0,N=c[v]):c[v]>=g?(B=C[c[v]-g],N=O[c[v]-g]):(B=96,N=0),w=1<<p-A,b=1<<R,x=b;do{b-=w,a[_+(T>>A)+b]=I<<24|B<<16|N|0}while(0!==b);for(w=1<<p-1;T&w;)w>>=1;if(0!==w?(T&=w-1,T+=w):T=0,v++,0==--U[p]){if(p===y)break;p=t[i+c[v]]}if(p>E&&(T&k)!==m){for(0===A&&(A=E),_+=x,R=p-A,Z=1<<R;R+A<y&&(Z-=U[R+A],!(Z<=0));)R++,Z<<=1;if(S+=1<<R,1===e&&S>852||2===e&&S>592)return 1;m=T&k,a[m]=E<<24|R<<16|_-r|0}}return 0!==T&&(a[_+T]=p-A<<24|64<<16|0),h.bits=E,0},h={Z_NO_FLUSH:0,Z_PARTIAL_FLUSH:1,Z_SYNC_FLUSH:2,Z_FULL_FLUSH:3,Z_FINISH:4,Z_BLOCK:5,Z_TREES:6,Z_OK:0,Z_STREAM_END:1,Z_NEED_DICT:2,Z_ERRNO:-1,Z_STREAM_ERROR:-2,Z_DATA_ERROR:-3,Z_MEM_ERROR:-4,Z_BUF_ERROR:-5,Z_NO_COMPRESSION:0,Z_BEST_SPEED:1,Z_BEST_COMPRESSION:9,Z_DEFAULT_COMPRESSION:-1,Z_FILTERED:1,Z_HUFFMAN_ONLY:2,Z_RLE:3,Z_FIXED:4,Z_DEFAULT_STRATEGY:0,Z_BINARY:0,Z_TEXT:1,Z_UNKNOWN:2,Z_DEFLATED:8};const{Z_FINISH:u,Z_BLOCK:w,Z_TREES:b,Z_OK:m,Z_STREAM_END:k,Z_NEED_DICT:_,Z_STREAM_ERROR:g,Z_DATA_ERROR:p,Z_MEM_ERROR:v,Z_BUF_ERROR:x,Z_DEFLATED:y}=h,E=16180,R=16190,A=16191,Z=16192,S=16194,T=16199,O=16200,U=16206,D=16209,I=e=>(e>>>24&255)+(e>>>8&65280)+((65280&e)<<8)+((255&e)<<24);function B(){this.strm=null,this.mode=0,this.last=!1,this.wrap=0,this.havedict=!1,this.flags=0,this.dmax=0,this.check=0,this.total=0,this.head=null,this.wbits=0,this.wsize=0,this.whave=0,this.wnext=0,this.window=null,this.hold=0,this.bits=0,this.length=0,this.offset=0,this.extra=0,this.lencode=null,this.distcode=null,this.lenbits=0,this.distbits=0,this.ncode=0,this.nlen=0,this.ndist=0,this.have=0,this.next=null,this.lens=new Uint16Array(320),this.work=new Uint16Array(288),this.lendyn=null,this.distdyn=null,this.sane=0,this.back=0,this.was=0}const N=e=>{if(!e)return 1;const t=e.state;return!t||t.strm!==e||t.mode<E||t.mode>16211?1:0},C=e=>{if(N(e))return g;const t=e.state;return e.total_in=e.total_out=t.total=0,e.msg="",t.wrap&&(e.adler=1&t.wrap),t.mode=E,t.last=0,t.havedict=0,t.flags=-1,t.dmax=32768,t.head=null,t.hold=0,t.bits=0,t.lencode=t.lendyn=new Int32Array(852),t.distcode=t.distdyn=new Int32Array(592),t.sane=1,t.back=-1,m},z=e=>{if(N(e))return g;const t=e.state;return t.wsize=0,t.whave=0,t.wnext=0,C(e)},F=(e,t)=>{let i;if(N(e))return g;const n=e.state;return t<0?(i=0,t=-t):(i=5+(t>>4),t<48&&(t&=15)),t&&(t<8||t>15)?g:(null!==n.window&&n.wbits!==t&&(n.window=null),n.wrap=i,n.wbits=t,z(e))},L=(e,t)=>{if(!e)return g;const i=new B;e.state=i,i.strm=e,i.window=null,i.mode=E;const n=F(e,t);return n!==m&&(e.state=null),n};let M,H,j=!0;const K=e=>{if(j){M=new Int32Array(512),H=new Int32Array(32);let t=0;for(;t<144;)e.lens[t++]=8;for(;t<256;)e.lens[t++]=9;for(;t<280;)e.lens[t++]=7;for(;t<288;)e.lens[t++]=8;for(c(1,e.lens,0,288,M,0,e.work,{bits:9}),t=0;t<32;)e.lens[t++]=5;c(2,e.lens,0,32,H,0,e.work,{bits:5}),j=!1}e.lencode=M,e.lenbits=9,e.distcode=H,e.distbits=5},P=(e,t,i,n)=>{let a;const r=e.state;return null===r.window&&(r.wsize=1<<r.wbits,r.wnext=0,r.whave=0,r.window=new Uint8Array(r.wsize)),n>=r.wsize?(r.window.set(t.subarray(i-r.wsize,i),0),r.wnext=0,r.whave=r.wsize):(a=r.wsize-r.wnext,a>n&&(a=n),r.window.set(t.subarray(i-n,i-n+a),r.wnext),(n-=a)?(r.window.set(t.subarray(i-n,i),0),r.wnext=n,r.whave=r.wsize):(r.wnext+=a,r.wnext===r.wsize&&(r.wnext=0),r.whave<r.wsize&&(r.whave+=a))),0};var Y={inflateReset:z,inflateReset2:F,inflateResetKeep:C,inflateInit:e=>L(e,15),inflateInit2:L,inflate:(e,i)=>{let a,o,s,l,d,f,h,B,C,z,F,L,M,H,j,Y,G,X,W,q,J,Q,V=0;const $=new Uint8Array(4);let ee,te;const ie=new Uint8Array([16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15]);if(N(e)||!e.output||!e.input&&0!==e.avail_in)return g;a=e.state,a.mode===A&&(a.mode=Z),d=e.next_out,s=e.output,h=e.avail_out,l=e.next_in,o=e.input,f=e.avail_in,B=a.hold,C=a.bits,z=f,F=h,Q=m;e:for(;;)switch(a.mode){case E:if(0===a.wrap){a.mode=Z;break}for(;C<16;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(2&a.wrap&&35615===B){0===a.wbits&&(a.wbits=15),a.check=0,$[0]=255&B,$[1]=B>>>8&255,a.check=n(a.check,$,2,0),B=0,C=0,a.mode=16181;break}if(a.head&&(a.head.done=!1),!(1&a.wrap)||(((255&B)<<8)+(B>>8))%31){e.msg="incorrect header check",a.mode=D;break}if((15&B)!==y){e.msg="unknown compression method",a.mode=D;break}if(B>>>=4,C-=4,J=8+(15&B),0===a.wbits&&(a.wbits=J),J>15||J>a.wbits){e.msg="invalid window size",a.mode=D;break}a.dmax=1<<a.wbits,a.flags=0,e.adler=a.check=1,a.mode=512&B?16189:A,B=0,C=0;break;case 16181:for(;C<16;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(a.flags=B,(255&a.flags)!==y){e.msg="unknown compression method",a.mode=D;break}if(57344&a.flags){e.msg="unknown header flags set",a.mode=D;break}a.head&&(a.head.text=B>>8&1),512&a.flags&&4&a.wrap&&($[0]=255&B,$[1]=B>>>8&255,a.check=n(a.check,$,2,0)),B=0,C=0,a.mode=16182;case 16182:for(;C<32;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}a.head&&(a.head.time=B),512&a.flags&&4&a.wrap&&($[0]=255&B,$[1]=B>>>8&255,$[2]=B>>>16&255,$[3]=B>>>24&255,a.check=n(a.check,$,4,0)),B=0,C=0,a.mode=16183;case 16183:for(;C<16;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}a.head&&(a.head.xflags=255&B,a.head.os=B>>8),512&a.flags&&4&a.wrap&&($[0]=255&B,$[1]=B>>>8&255,a.check=n(a.check,$,2,0)),B=0,C=0,a.mode=16184;case 16184:if(1024&a.flags){for(;C<16;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}a.length=B,a.head&&(a.head.extra_len=B),512&a.flags&&4&a.wrap&&($[0]=255&B,$[1]=B>>>8&255,a.check=n(a.check,$,2,0)),B=0,C=0}else a.head&&(a.head.extra=null);a.mode=16185;case 16185:if(1024&a.flags&&(L=a.length,L>f&&(L=f),L&&(a.head&&(J=a.head.extra_len-a.length,a.head.extra||(a.head.extra=new Uint8Array(a.head.extra_len)),a.head.extra.set(o.subarray(l,l+L),J)),512&a.flags&&4&a.wrap&&(a.check=n(a.check,o,L,l)),f-=L,l+=L,a.length-=L),a.length))break e;a.length=0,a.mode=16186;case 16186:if(2048&a.flags){if(0===f)break e;L=0;do{J=o[l+L++],a.head&&J&&a.length<65536&&(a.head.name+=String.fromCharCode(J))}while(J&&L<f);if(512&a.flags&&4&a.wrap&&(a.check=n(a.check,o,L,l)),f-=L,l+=L,J)break e}else a.head&&(a.head.name=null);a.length=0,a.mode=16187;case 16187:if(4096&a.flags){if(0===f)break e;L=0;do{J=o[l+L++],a.head&&J&&a.length<65536&&(a.head.comment+=String.fromCharCode(J))}while(J&&L<f);if(512&a.flags&&4&a.wrap&&(a.check=n(a.check,o,L,l)),f-=L,l+=L,J)break e}else a.head&&(a.head.comment=null);a.mode=16188;case 16188:if(512&a.flags){for(;C<16;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(4&a.wrap&&B!==(65535&a.check)){e.msg="header crc mismatch",a.mode=D;break}B=0,C=0}a.head&&(a.head.hcrc=a.flags>>9&1,a.head.done=!0),e.adler=a.check=0,a.mode=A;break;case 16189:for(;C<32;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}e.adler=a.check=I(B),B=0,C=0,a.mode=R;case R:if(0===a.havedict)return e.next_out=d,e.avail_out=h,e.next_in=l,e.avail_in=f,a.hold=B,a.bits=C,_;e.adler=a.check=1,a.mode=A;case A:if(i===w||i===b)break e;case Z:if(a.last){B>>>=7&C,C-=7&C,a.mode=U;break}for(;C<3;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}switch(a.last=1&B,B>>>=1,C-=1,3&B){case 0:a.mode=16193;break;case 1:if(K(a),a.mode=T,i===b){B>>>=2,C-=2;break e}break;case 2:a.mode=16196;break;case 3:e.msg="invalid block type",a.mode=D}B>>>=2,C-=2;break;case 16193:for(B>>>=7&C,C-=7&C;C<32;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if((65535&B)!=(B>>>16^65535)){e.msg="invalid stored block lengths",a.mode=D;break}if(a.length=65535&B,B=0,C=0,a.mode=S,i===b)break e;case S:a.mode=16195;case 16195:if(L=a.length,L){if(L>f&&(L=f),L>h&&(L=h),0===L)break e;s.set(o.subarray(l,l+L),d),f-=L,l+=L,h-=L,d+=L,a.length-=L;break}a.mode=A;break;case 16196:for(;C<14;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(a.nlen=257+(31&B),B>>>=5,C-=5,a.ndist=1+(31&B),B>>>=5,C-=5,a.ncode=4+(15&B),B>>>=4,C-=4,a.nlen>286||a.ndist>30){e.msg="too many length or distance symbols",a.mode=D;break}a.have=0,a.mode=16197;case 16197:for(;a.have<a.ncode;){for(;C<3;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}a.lens[ie[a.have++]]=7&B,B>>>=3,C-=3}for(;a.have<19;)a.lens[ie[a.have++]]=0;if(a.lencode=a.lendyn,a.lenbits=7,ee={bits:a.lenbits},Q=c(0,a.lens,0,19,a.lencode,0,a.work,ee),a.lenbits=ee.bits,Q){e.msg="invalid code lengths set",a.mode=D;break}a.have=0,a.mode=16198;case 16198:for(;a.have<a.nlen+a.ndist;){for(;V=a.lencode[B&(1<<a.lenbits)-1],j=V>>>24,Y=V>>>16&255,G=65535&V,!(j<=C);){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(G<16)B>>>=j,C-=j,a.lens[a.have++]=G;else{if(16===G){for(te=j+2;C<te;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(B>>>=j,C-=j,0===a.have){e.msg="invalid bit length repeat",a.mode=D;break}J=a.lens[a.have-1],L=3+(3&B),B>>>=2,C-=2}else if(17===G){for(te=j+3;C<te;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}B>>>=j,C-=j,J=0,L=3+(7&B),B>>>=3,C-=3}else{for(te=j+7;C<te;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}B>>>=j,C-=j,J=0,L=11+(127&B),B>>>=7,C-=7}if(a.have+L>a.nlen+a.ndist){e.msg="invalid bit length repeat",a.mode=D;break}for(;L--;)a.lens[a.have++]=J}}if(a.mode===D)break;if(0===a.lens[256]){e.msg="invalid code -- missing end-of-block",a.mode=D;break}if(a.lenbits=9,ee={bits:a.lenbits},Q=c(1,a.lens,0,a.nlen,a.lencode,0,a.work,ee),a.lenbits=ee.bits,Q){e.msg="invalid literal/lengths set",a.mode=D;break}if(a.distbits=6,a.distcode=a.distdyn,ee={bits:a.distbits},Q=c(2,a.lens,a.nlen,a.ndist,a.distcode,0,a.work,ee),a.distbits=ee.bits,Q){e.msg="invalid distances set",a.mode=D;break}if(a.mode=T,i===b)break e;case T:a.mode=O;case O:if(f>=6&&h>=258){e.next_out=d,e.avail_out=h,e.next_in=l,e.avail_in=f,a.hold=B,a.bits=C,r(e,F),d=e.next_out,s=e.output,h=e.avail_out,l=e.next_in,o=e.input,f=e.avail_in,B=a.hold,C=a.bits,a.mode===A&&(a.back=-1);break}for(a.back=0;V=a.lencode[B&(1<<a.lenbits)-1],j=V>>>24,Y=V>>>16&255,G=65535&V,!(j<=C);){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(Y&&0==(240&Y)){for(X=j,W=Y,q=G;V=a.lencode[q+((B&(1<<X+W)-1)>>X)],j=V>>>24,Y=V>>>16&255,G=65535&V,!(X+j<=C);){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}B>>>=X,C-=X,a.back+=X}if(B>>>=j,C-=j,a.back+=j,a.length=G,0===Y){a.mode=16205;break}if(32&Y){a.back=-1,a.mode=A;break}if(64&Y){e.msg="invalid literal/length code",a.mode=D;break}a.extra=15&Y,a.mode=16201;case 16201:if(a.extra){for(te=a.extra;C<te;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}a.length+=B&(1<<a.extra)-1,B>>>=a.extra,C-=a.extra,a.back+=a.extra}a.was=a.length,a.mode=16202;case 16202:for(;V=a.distcode[B&(1<<a.distbits)-1],j=V>>>24,Y=V>>>16&255,G=65535&V,!(j<=C);){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(0==(240&Y)){for(X=j,W=Y,q=G;V=a.distcode[q+((B&(1<<X+W)-1)>>X)],j=V>>>24,Y=V>>>16&255,G=65535&V,!(X+j<=C);){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}B>>>=X,C-=X,a.back+=X}if(B>>>=j,C-=j,a.back+=j,64&Y){e.msg="invalid distance code",a.mode=D;break}a.offset=G,a.extra=15&Y,a.mode=16203;case 16203:if(a.extra){for(te=a.extra;C<te;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}a.offset+=B&(1<<a.extra)-1,B>>>=a.extra,C-=a.extra,a.back+=a.extra}if(a.offset>a.dmax){e.msg="invalid distance too far back",a.mode=D;break}a.mode=16204;case 16204:if(0===h)break e;if(L=F-h,a.offset>L){if(L=a.offset-L,L>a.whave&&a.sane){e.msg="invalid distance too far back",a.mode=D;break}L>a.wnext?(L-=a.wnext,M=a.wsize-L):M=a.wnext-L,L>a.length&&(L=a.length),H=a.window}else H=s,M=d-a.offset,L=a.length;L>h&&(L=h),h-=L,a.length-=L;do{s[d++]=H[M++]}while(--L);0===a.length&&(a.mode=O);break;case 16205:if(0===h)break e;s[d++]=a.length,h--,a.mode=O;break;case U:if(a.wrap){for(;C<32;){if(0===f)break e;f--,B|=o[l++]<<C,C+=8}if(F-=h,e.total_out+=F,a.total+=F,4&a.wrap&&F&&(e.adler=a.check=a.flags?n(a.check,s,F,d-F):t(a.check,s,F,d-F)),F=h,4&a.wrap&&(a.flags?B:I(B))!==a.check){e.msg="incorrect data check",a.mode=D;break}B=0,C=0}a.mode=16207;case 16207:if(a.wrap&&a.flags){for(;C<32;){if(0===f)break e;f--,B+=o[l++]<<C,C+=8}if(4&a.wrap&&B!==(4294967295&a.total)){e.msg="incorrect length check",a.mode=D;break}B=0,C=0}a.mode=16208;case 16208:Q=k;break e;case D:Q=p;break e;case 16210:return v;default:return g}return e.next_out=d,e.avail_out=h,e.next_in=l,e.avail_in=f,a.hold=B,a.bits=C,(a.wsize||F!==e.avail_out&&a.mode<D&&(a.mode<U||i!==u))&&P(e,e.output,e.next_out,F-e.avail_out),z-=e.avail_in,F-=e.avail_out,e.total_in+=z,e.total_out+=F,a.total+=F,4&a.wrap&&F&&(e.adler=a.check=a.flags?n(a.check,s,F,e.next_out-F):t(a.check,s,F,e.next_out-F)),e.data_type=a.bits+(a.last?64:0)+(a.mode===A?128:0)+(a.mode===T||a.mode===S?256:0),(0===z&&0===F||i===u)&&Q===m&&(Q=x),Q},inflateEnd:e=>{if(N(e))return g;let t=e.state;return t.window&&(t.window=null),e.state=null,m},inflateGetHeader:(e,t)=>{if(N(e))return g;const i=e.state;return 0==(2&i.wrap)?g:(i.head=t,t.done=!1,m)},inflateSetDictionary:(e,i)=>{const n=i.length;let a,r,o;return N(e)?g:(a=e.state,0!==a.wrap&&a.mode!==R?g:a.mode===R&&(r=1,r=t(r,i,n,0),r!==a.check)?p:(o=P(e,i,n,n),o?(a.mode=16210,v):(a.havedict=1,m)))},inflateInfo:"pako inflate (from Nodeca project)"};const G=(e,t)=>Object.prototype.hasOwnProperty.call(e,t);var X=function(e){const t=Array.prototype.slice.call(arguments,1);for(;t.length;){const i=t.shift();if(i){if("object"!=typeof i)throw new TypeError(i+"must be non-object");for(const t in i)G(i,t)&&(e[t]=i[t])}}return e},W=e=>{let t=0;for(let i=0,n=e.length;i<n;i++)t+=e[i].length;const i=new Uint8Array(t);for(let t=0,n=0,a=e.length;t<a;t++){let a=e[t];i.set(a,n),n+=a.length}return i};let q=!0;try{String.fromCharCode.apply(null,new Uint8Array(1))}catch(e){q=!1}const J=new Uint8Array(256);for(let e=0;e<256;e++)J[e]=e>=252?6:e>=248?5:e>=240?4:e>=224?3:e>=192?2:1;J[254]=J[254]=1;var Q=e=>{if("function"==typeof TextEncoder&&TextEncoder.prototype.encode)return(new TextEncoder).encode(e);let t,i,n,a,r,o=e.length,s=0;for(a=0;a<o;a++)i=e.charCodeAt(a),55296==(64512&i)&&a+1<o&&(n=e.charCodeAt(a+1),56320==(64512&n)&&(i=65536+(i-55296<<10)+(n-56320),a++)),s+=i<128?1:i<2048?2:i<65536?3:4;for(t=new Uint8Array(s),r=0,a=0;r<s;a++)i=e.charCodeAt(a),55296==(64512&i)&&a+1<o&&(n=e.charCodeAt(a+1),56320==(64512&n)&&(i=65536+(i-55296<<10)+(n-56320),a++)),i<128?t[r++]=i:i<2048?(t[r++]=192|i>>>6,t[r++]=128|63&i):i<65536?(t[r++]=224|i>>>12,t[r++]=128|i>>>6&63,t[r++]=128|63&i):(t[r++]=240|i>>>18,t[r++]=128|i>>>12&63,t[r++]=128|i>>>6&63,t[r++]=128|63&i);return t},V=(e,t)=>{const i=t||e.length;if("function"==typeof TextDecoder&&TextDecoder.prototype.decode)return(new TextDecoder).decode(e.subarray(0,t));let n,a;const r=new Array(2*i);for(a=0,n=0;n<i;){let t=e[n++];if(t<128){r[a++]=t;continue}let o=J[t];if(o>4)r[a++]=65533,n+=o-1;else{for(t&=2===o?31:3===o?15:7;o>1&&n<i;)t=t<<6|63&e[n++],o--;o>1?r[a++]=65533:t<65536?r[a++]=t:(t-=65536,r[a++]=55296|t>>10&1023,r[a++]=56320|1023&t)}}return((e,t)=>{if(t<65534&&e.subarray&&q)return String.fromCharCode.apply(null,e.length===t?e:e.subarray(0,t));let i="";for(let n=0;n<t;n++)i+=String.fromCharCode(e[n]);return i})(r,a)},$=(e,t)=>{(t=t||e.length)>e.length&&(t=e.length);let i=t-1;for(;i>=0&&128==(192&e[i]);)i--;return i<0||0===i?t:i+J[e[i]]>t?i:t},ee={2:"need dictionary",1:"stream end",0:"","-1":"file error","-2":"stream error","-3":"data error","-4":"insufficient memory","-5":"buffer error","-6":"incompatible version"};var te=function(){this.input=null,this.next_in=0,this.avail_in=0,this.total_in=0,this.output=null,this.next_out=0,this.avail_out=0,this.total_out=0,this.msg="",this.state=null,this.data_type=2,this.adler=0};var ie=function(){this.text=0,this.time=0,this.xflags=0,this.os=0,this.extra=null,this.extra_len=0,this.name="",this.comment="",this.hcrc=0,this.done=!1};const ne=Object.prototype.toString,{Z_NO_FLUSH:ae,Z_FINISH:re,Z_OK:oe,Z_STREAM_END:se,Z_NEED_DICT:le,Z_STREAM_ERROR:de,Z_DATA_ERROR:fe,Z_MEM_ERROR:ce}=h;function he(e){this.options=X({chunkSize:65536,windowBits:15,to:""},e||{});const t=this.options;t.raw&&t.windowBits>=0&&t.windowBits<16&&(t.windowBits=-t.windowBits,0===t.windowBits&&(t.windowBits=-15)),!(t.windowBits>=0&&t.windowBits<16)||e&&e.windowBits||(t.windowBits+=32),t.windowBits>15&&t.windowBits<48&&0==(15&t.windowBits)&&(t.windowBits|=15),this.err=0,this.msg="",this.ended=!1,this.chunks=[],this.strm=new te,this.strm.avail_out=0;let i=Y.inflateInit2(this.strm,t.windowBits);if(i!==oe)throw new Error(ee[i]);if(this.header=new ie,Y.inflateGetHeader(this.strm,this.header),t.dictionary&&("string"==typeof t.dictionary?t.dictionary=Q(t.dictionary):"[object ArrayBuffer]"===ne.call(t.dictionary)&&(t.dictionary=new Uint8Array(t.dictionary)),t.raw&&(i=Y.inflateSetDictionary(this.strm,t.dictionary),i!==oe)))throw new Error(ee[i])}function ue(e,t){const i=new he(t);if(i.push(e),i.err)throw i.msg||ee[i.err];return i.result}he.prototype.push=function(e,t){const i=this.strm,n=this.options.chunkSize,a=this.options.dictionary;let r,o,s;if(this.ended)return!1;for(o=t===~~t?t:!0===t?re:ae,"[object ArrayBuffer]"===ne.call(e)?i.input=new Uint8Array(e):i.input=e,i.next_in=0,i.avail_in=i.input.length;;){for(0===i.avail_out&&(i.output=new Uint8Array(n),i.next_out=0,i.avail_out=n),r=Y.inflate(i,o),r===le&&a&&(r=Y.inflateSetDictionary(i,a),r===oe?r=Y.inflate(i,o):r===fe&&(r=le));i.avail_in>0&&r===se&&i.state.wrap>0&&0!==e[i.next_in];)Y.inflateReset(i),r=Y.inflate(i,o);switch(r){case de:case fe:case le:case ce:return this.onEnd(r),this.ended=!0,!1}if(s=i.avail_out,i.next_out&&(0===i.avail_out||r===se))if("string"===this.options.to){let e=$(i.output,i.next_out),t=i.next_out-e,a=V(i.output,e);i.next_out=t,i.avail_out=n-t,t&&i.output.set(i.output.subarray(e,e+t),0),this.onData(a)}else this.onData(i.output.length===i.next_out?i.output:i.output.subarray(0,i.next_out));if(r!==oe||0!==s){if(r===se)return r=Y.inflateEnd(this.strm),this.onEnd(r),this.ended=!0,!0;if(0===i.avail_in)break}}return!0},he.prototype.onData=function(e){this.chunks.push(e)},he.prototype.onEnd=function(e){e===oe&&("string"===this.options.to?this.result=this.chunks.join(""):this.result=W(this.chunks)),this.chunks=[],this.err=e,this.msg=this.strm.msg};var we=he,be=ue,me=function(e,t){return(t=t||{}).raw=!0,ue(e,t)},ke=ue,_e=h,ge={Inflate:we,inflate:be,inflateRaw:me,ungzip:ke,constants:_e};e.Inflate=we,e.constants=_e,e.default=ge,e.inflate=be,e.inflateRaw=me,e.ungzip=ke,Object.defineProperty(e,"__esModule",{value:!0})}));

(function(){
  "use strict";

  // =================================================================
  // ReportStore - the report's data layer.
  //
  // Every run's data is embedded in this HTML, compressed (gzip, ~90%+
  // smaller) and dictionary-packed (repeated column values like Category/
  // Workset/Creator stored once, referenced by index) - so the file stays
  // in the single-digit-to-low-tens of MB even after 100+ exports on a
  // real model, instead of growing without bound. The browser decompresses
  // it once at load (native DecompressionStream, or the bundled pako
  // fallback above) and everything - every date, every comparison, the
  // Evolution chart, every tracked parameter - is available immediately.
  // No folder to pick, ever; nothing is read from disk at view-time.
  //
  // (Two earlier designs both had a real cost: embedding every run's FULL
  // uncompressed data made the HTML grow past ~500MB and stop opening;
  // embedding only the latest run and asking for a folder pick to see
  // history worked, but meant re-picking a folder each session just to
  // compare two dates. Compression + dictionary-packing removes both -
  // full history, small file, zero clicks to see it.)
  // =================================================================
  var ReportStore = (function(){
    var snapshotFiles = {};   // displayLabel -> File (only used by the fallback folder-picker path, see the bottom of this file)
    var deltas = [];          // [{date, changes}], sorted chronologically
    var baseline = {};        // eid -> {paramName: value} (fallback path only)
    var availableParams = [];  // fallback path only (getAvailableParams() also checks embeddedAvailableParams)
    var cache = {};           // displayLabel -> {columns, rows} - fully decoded, ready to use
    var rawSnapshots = {};    // displayLabel -> still-encoded {columns, dict, rows} from the payload,
                               // decoded into `cache` lazily on first actual use (see getSnapshot) -
                               // decoding every historical run up front, before the page even shows
                               // anything, is exactly the kind of work that makes a 100+-run project
                               // feel like it's freezing on open for no visible reason.
    var embeddedLatestKey = null; // the embedded payload's latest date label
    var embeddedAvailableParams = []; // param names from the embedded payload
    var folderLoaded = false;     // true once either the embedded payload or the fallback folder is loaded

    // Filenames use a filesystem-safe label (YYYY-MM-DD_HH-MM-SS); every
    // date shown in the UI (and every ParamsDelta "date" field) uses the
    // display form (YYYY-MM-DD HH:MM:SS) - this is a pure string
    // transform, not a real date parse, so it can't be thrown off by
    // timezones or locale (matches the same transform on the Python and
    // C# export side exactly).
    function displayLabelFromFile(fileLabel) {
      var m = /^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})$/.exec(fileLabel);
      return m ? (m[1] + ' ' + m[2] + ':' + m[3] + ':' + m[4]) : fileLabel;
    }

    function readFileText(file) {
      if (file.text) return file.text();
      // Fallback for a File implementation without .text() (older but
      // still Promise-capable engines) - same result via FileReader.
      return new Promise(function(resolve, reject){
        var reader = new FileReader();
        reader.onload = function(){ resolve(reader.result); };
        reader.onerror = function(){ reject(reader.error || new Error('read error')); };
        reader.readAsText(file, 'utf-8');
      });
    }

    // RFC4180-style comma CSV, matching exactly what the Python (csv
    // module, default dialect) and C# (WriteCsv/CsvEscape) export sides
    // both write: comma-delimited, double-quote quoting, "" for an
    // embedded quote, CRLF line endings.
    function parseCsvText(text) {
      if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1); // strip a stray BOM defensively
      var rows = [], row = [], field = '', inQuotes = false;
      var i = 0, len = text.length;
      while (i < len) {
        var c = text.charAt(i);
        if (inQuotes) {
          if (c === '"') {
            if (text.charAt(i + 1) === '"') { field += '"'; i += 2; continue; }
            inQuotes = false; i++; continue;
          }
          field += c; i++; continue;
        }
        if (c === '"') { inQuotes = true; i++; continue; }
        if (c === ',') { row.push(field); field = ''; i++; continue; }
        if (c === '\r' || c === '\n') {
          if (c === '\r' && text.charAt(i + 1) === '\n') i++;
          row.push(field); field = ''; rows.push(row); row = []; i++; continue;
        }
        field += c; i++;
      }
      if (field !== '' || row.length > 0) { row.push(field); rows.push(row); }
      // A file ending in a newline produces one trailing all-empty row -
      // drop only that specific artifact, not any row that legitimately
      // has one empty field.
      if (rows.length && rows[rows.length - 1].length === 1 && rows[rows.length - 1][0] === '') rows.pop();
      if (rows.length === 0) return {columns: [], rows: []};
      var header = rows[0], dataRows = [];
      for (var r = 1; r < rows.length; r++) { if (rows[r].length === header.length) dataRows.push(rows[r]); }
      return {columns: header, rows: dataRows};
    }

    // A dictionary-packed snapshot -> {columns, rows}. Each column's
    // repeated values (Category, Workset, Creator, ...) are stored once in
    // dict[colIndex] and rows hold indices into it - undoing that here is
    // the only "decoding" the compressed payload needs (JSON.parse already
    // did the rest). A snapshot with no "dict" key is already plain
    // {columns, rows} (the fallback folder-picker path never dict-packs).
    function decodeSnapshot(enc) {
      if (!enc) return {columns: [], rows: []};
      if (!enc.dict) return {columns: enc.columns, rows: enc.rows || []};
      var dict = enc.dict;
      var rows = (enc.rows || []).map(function(ixRow){
        var out = new Array(ixRow.length);
        for (var i = 0; i < ixRow.length; i++) out[i] = dict[i][ixRow[i]];
        return out;
      });
      return {columns: enc.columns, rows: rows};
    }

    // Seed EVERYTHING from the decompressed inline payload - every run's
    // snapshot, every parameter delta, every tracked parameter name.
    // Called once at boot, before any tab is set up; after this the report
    // behaves exactly as if a folder had been picked and every file in it
    // read, except none of that ever happened.
    //
    // Snapshots are kept RAW (still dictionary-encoded) here and decoded
    // one at a time in getSnapshot() the first time each is actually asked
    // for - see the boot sequence at the bottom of this file, which decodes
    // only the latest one (synchronously, before the tabs are set up) and
    // leaves the rest for on-demand access (an older date, Compare, the
    // Evolution chart). Decoding is cheap per snapshot, but decoding ALL of
    // them up front on a project with 100+ runs is real, visible work with
    // nothing to show for it until it finishes - deferring it is what keeps
    // opening the report fast no matter how much history it holds.
    function seedAll(payload) {
      if (!payload || !payload.latest) return;
      embeddedLatestKey = payload.latest;
      rawSnapshots = payload.snapshots || {};
      deltas = (payload.deltas || []).slice().sort(function(a, b){ return a.date < b.date ? -1 : (a.date > b.date ? 1 : 0); });
      embeddedAvailableParams = payload.availableParams || [];
      folderLoaded = true; // everything needed is already in memory (just not all decoded yet)
    }

    function loadFiles(fileList) {
      snapshotFiles = {}; deltas = []; baseline = {}; availableParams = [];
      // Keep the already-parsed embedded snapshot - the folder holds the
      // OLDER runs; re-reading the current one from its csv would be waste.
      var keepEmbedded = embeddedLatestKey ? cache[embeddedLatestKey] : null;
      cache = {};
      if (keepEmbedded) cache[embeddedLatestKey] = keepEmbedded;
      var snapRe = /^Snapshot_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.csv$/;
      var deltaRe = /^ParamsDelta_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.json$/;
      var deltaFiles = [], baselineFile = null;
      for (var i = 0; i < fileList.length; i++) {
        var f = fileList[i], name = f.name, m;
        if ((m = snapRe.exec(name))) { snapshotFiles[displayLabelFromFile(m[1])] = f; continue; }
        if (deltaRe.test(name)) { deltaFiles.push(f); continue; }
        if (name === '_params_baseline.json') { baselineFile = f; continue; }
      }
      if (Object.keys(snapshotFiles).length === 0) {
        return Promise.reject(new Error(
          'Aucun fichier Snapshot_*.csv trouve dans ce dossier - choisissez le dossier ' +
          'de sortie utilise par le script Dynamo ou le plugin (celui qui contient ces fichiers).'));
      }
      var tasks = [];
      if (baselineFile) {
        tasks.push(readFileText(baselineFile).then(function(t){
          try { var parsed = JSON.parse(t); if (parsed && typeof parsed === 'object') baseline = parsed; } catch (e) {}
        }));
      }
      var deltaResults = [];
      deltaFiles.forEach(function(f){
        tasks.push(readFileText(f).then(function(t){
          try {
            var d = JSON.parse(t);
            if (d && d.changes) deltaResults.push({date: d.date || '', changes: d.changes});
          } catch (e) {}
        }));
      });
      return Promise.all(tasks).then(function(){
        deltas = deltaResults.sort(function(a, b){ return a.date < b.date ? -1 : (a.date > b.date ? 1 : 0); });
        var seen = {};
        for (var eid in baseline) {
          if (!Object.prototype.hasOwnProperty.call(baseline, eid)) continue;
          for (var p in baseline[eid]) { if (Object.prototype.hasOwnProperty.call(baseline[eid], p)) seen[p] = true; }
        }
        availableParams = Object.keys(seen).sort();
        folderLoaded = true;
        var keys = dateKeys();
        return {snapshotCount: keys.length, latestKey: keys[keys.length - 1]};
      });
    }

    function dateKeys() {
      var set = {};
      Object.keys(snapshotFiles).forEach(function(k){ set[k] = 1; });
      Object.keys(rawSnapshots).forEach(function(k){ set[k] = 1; }); // embedded, not decoded yet
      Object.keys(cache).forEach(function(k){ set[k] = 1; }); // embedded (or fallback-folder) AND decoded
      if (embeddedLatestKey) set[embeddedLatestKey] = 1;
      return Object.keys(set).sort();
    }
    function latestKey() { var k = dateKeys(); return k.length ? k[k.length - 1] : null; }
    // Synchronous - only ever returns something for a date already decoded
    // (the latest one, guaranteed by the boot sequence; anything else once
    // some earlier getSnapshot() call has resolved for it).
    function getCached(label) { return cache[label]; }
    function hasFolder() { return folderLoaded; }
    function hasEmbedded() { return !!embeddedLatestKey; }
    function getSnapshot(label) {
      if (cache[label]) return Promise.resolve(cache[label]);
      if (Object.prototype.hasOwnProperty.call(rawSnapshots, label)) {
        var decoded = decodeSnapshot(rawSnapshots[label]);
        cache[label] = decoded;
        return Promise.resolve(decoded);
      }
      var f = snapshotFiles[label];
      if (!f) {
        // Not the embedded one and no folder yet - the caller should ask
        // the user to pick the export folder, then retry.
        if (!folderLoaded) { var e = new Error('NEED_FOLDER'); e.needFolder = true; return Promise.reject(e); }
        return Promise.reject(new Error('Instantane introuvable : ' + label));
      }
      return readFileText(f).then(function(text){
        var parsed = parseCsvText(text);
        cache[label] = parsed;
        return parsed;
      });
    }
    // Sequentially loads (and caches) every label in `labels`, reporting
    // progress as it goes - used by the Evolution chart, the one view
    // that genuinely needs many/all historical snapshots at once.
    // Sequential (not parallel) on purpose: keeps memory/handle use flat
    // and progress reporting simple even with 100+ files.
    function getMany(labels, onProgress) {
      var result = {}, i = 0;
      function next() {
        if (i >= labels.length) return Promise.resolve(result);
        var k = labels[i];
        return getSnapshot(k).then(function(snap){
          result[k] = snap;
          i++;
          if (onProgress) onProgress(i, labels.length);
          return next();
        });
      }
      return next();
    }

    return {
      seedAll: seedAll,
      loadFiles: loadFiles,
      dateKeys: dateKeys,
      latestKey: latestKey,
      getCached: getCached,
      hasFolder: hasFolder,
      hasEmbedded: hasEmbedded,
      getSnapshot: getSnapshot,
      getMany: getMany,
      getDeltas: function(){ return deltas; },
      getAvailableParams: function(){
        if (!embeddedAvailableParams.length) return availableParams;
        if (!availableParams.length) return embeddedAvailableParams;
        var seen = {}, merged = [];
        embeddedAvailableParams.concat(availableParams).forEach(function(p){ if (!seen[p]) { seen[p] = true; merged.push(p); } });
        return merged.sort();
      }
    };
  })();

  // =================================================================
  // Folder prompt - shared by every history feature. The first time one
  // of them runs without the export folder loaded, this pops the folder
  // picker; on success it loads the older runs/deltas/baseline and fires
  // every registered "after folder" callback (each tab uses one to
  // re-fill its date dropdowns and unlock its history controls). All
  // later calls resolve instantly.
  // =================================================================
  var afterFolderCallbacks = [];
  function onAfterFolder(fn) { afterFolderCallbacks.push(fn); }
  var pendingFolderPrompt = null;
  function promptForFolder() {
    if (ReportStore.hasFolder()) return Promise.resolve();
    if (pendingFolderPrompt) return pendingFolderPrompt;
    var input = document.getElementById('loadGateInput');
    pendingFolderPrompt = new Promise(function(resolve, reject){
      var done = false;
      function onChange() {
        if (done) return; done = true;
        input.removeEventListener('change', onChange);
        var files = input.files;
        if (!files || files.length === 0) { pendingFolderPrompt = null; reject(new Error('annule')); return; }
        ReportStore.loadFiles(files).then(function(){
          return ReportStore.getSnapshot(ReportStore.latestKey());
        }).then(function(){
          document.body.className = (document.body.className + ' folder-loaded').trim();
          afterFolderCallbacks.forEach(function(fn){ try { fn(); } catch (e) {} });
          resolve();
        }, function(err){
          pendingFolderPrompt = null;
          alert((err && err.message) ? err.message : 'Erreur de lecture du dossier choisi.');
          reject(err);
        });
      }
      input.value = ''; // let the same folder be re-picked if needed
      input.addEventListener('change', onChange);
      input.click();
    });
    return pendingFolderPrompt;
  }

  // =================================================================
  // Small shared utilities (used by both tabs)
  // =================================================================

  // Plain-object based Set replacement - avoids relying on the native
  // Set object, which some older/embedded browser rendering engines
  // (e.g. an outdated WebView used to open local HTML files) do not
  // support, silently breaking every filter.
  function makeValueSet(initialArray) {
    var store = {};
    if (initialArray) {
      for (var i = 0; i < initialArray.length; i++) store['$' + initialArray[i]] = true;
    }
    return {
      has: function(v) { return Object.prototype.hasOwnProperty.call(store, '$' + v); },
      add: function(v) { store['$' + v] = true; },
      remove: function(v) { delete store['$' + v]; },
      clear: function() { store = {}; },
      count: function() {
        var n = 0;
        for (var k in store) { if (Object.prototype.hasOwnProperty.call(store, k)) n++; }
        return n;
      }
    };
  }

  function simpleTextSort(a, b) {
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  }

  function debounce(fn, ms) {
    var t = null;
    return function() {
      var args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function(){ fn.apply(ctx, args); }, ms);
    };
  }

  function csvEscape(v) {
    var s = (v === null || v === undefined) ? '' : String(v);
    var needQuote = (s.indexOf('"') !== -1) || (s.indexOf(';') !== -1) || (s.indexOf(',') !== -1) ||
      (s.indexOf(String.fromCharCode(10)) !== -1) || (s.indexOf(String.fromCharCode(13)) !== -1);
    if (needQuote) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function exportRowsToExcel(columns, rows, filenamePrefix) {
    var lines = [];
    lines.push(columns.map(csvEscape).join(';'));
    for (var r = 0; r < rows.length; r++) lines.push(rows[r].map(csvEscape).join(';'));
    var content = String.fromCharCode(0xFEFF) + lines.join(String.fromCharCode(13) + String.fromCharCode(10));
    var blob = new Blob([content], {type: 'text/csv;charset=utf-8;'});
    var d = new Date();
    function pad(n){ return (n < 10 ? '0' : '') + n; }
    var name = filenamePrefix + '_' + d.getFullYear() + pad(d.getMonth()+1) + pad(d.getDate()) + '_' +
      pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()) + '.csv';
    if (window.navigator && window.navigator.msSaveBlob) { window.navigator.msSaveBlob(blob, name); return; }
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(a.href); }, 100);
  }

  // Copy text to the clipboard. Tries the modern Clipboard API first, but
  // that API can be refused on a file:// page depending on the browser's
  // permission policy - so it always falls back to the classic hidden-
  // textarea + execCommand('copy') trick, which works offline everywhere.
  function copyTextToClipboard(text, callback) {
    var settled = false;
    function finish(ok) { if (settled) return; settled = true; callback(ok); }
    function fallbackCopy() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.top = '0';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      finish(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function(){ finish(true); }, fallbackCopy);
      // Safety net: in some restricted/automated sessions the permission
      // check behind writeText() can stay pending forever instead of
      // rejecting - never leave the button stuck waiting on it.
      setTimeout(fallbackCopy, 800);
    } else {
      fallbackCopy();
    }
  }

  // localStorage wrapper with an in-memory fallback (private browsing /
  // disabled storage should degrade, not crash the report).
  var memStore = {};
  var Store = {
    get: function(key, fallback) {
      try {
        var raw = window.localStorage.getItem(key);
        return raw !== null ? JSON.parse(raw) : fallback;
      } catch (e) {
        return (key in memStore) ? memStore[key] : fallback;
      }
    },
    set: function(key, value) {
      try { window.localStorage.setItem(key, JSON.stringify(value)); }
      catch (e) { memStore[key] = value; }
    }
  };

  // ---- Theme (shared across both tabs, remembered) ----
  function applyTheme(mode) {
    document.body.className = document.body.className.replace(/\bdark\b/, '').trim();
    if (mode === 'dark') document.body.className = (document.body.className + ' dark').trim();
    Store.set('revitReportTheme', mode);
    var dk = document.getElementById('themeDarkBtn'), lt = document.getElementById('themeLightBtn');
    if (dk) dk.className = mode === 'dark' ? 'primary' : '';
    if (lt) lt.className = mode === 'light' ? 'primary' : '';
  }
  applyTheme(Store.get('revitReportTheme', 'light'));
  document.getElementById('themeDarkBtn').addEventListener('click', function(){ applyTheme('dark'); });
  document.getElementById('themeLightBtn').addEventListener('click', function(){ applyTheme('light'); });

  // =================================================================
  // Floating filter panel - ONE shared DOM node reused by every column
  // of every table. Positioned with position:fixed from the toggle
  // button's own bounding box, so it is never clipped by a scrolling/
  // sticky ancestor (that clipping is what forced v1's header to give
  // up position:sticky - see the v2 notes at the top of this file).
  // =================================================================
  var floatEl = document.getElementById('filterFloat');
  var floatOwner = null; // {table, colIndex, anchorBtn} - the column currently open

  function closeFloat() {
    floatEl.style.display = 'none';
    floatOwner = null;
  }
  function repositionFloat() {
    if (!floatOwner || floatEl.style.display !== 'block') return;
    var r = floatOwner.anchorBtn.getBoundingClientRect();
    var top = r.bottom + 4;
    var left = r.left;
    var maxLeft = window.innerWidth - floatEl.offsetWidth - 8;
    if (left > maxLeft) left = Math.max(8, maxLeft);
    floatEl.style.top = top + 'px';
    floatEl.style.left = left + 'px';
  }
  function openFloatFor(anchorBtn, table, colIndex) {
    floatOwner = {table: table, colIndex: colIndex, anchorBtn: anchorBtn};
    table.renderFilterPanel(colIndex, floatEl);
    floatEl.style.display = 'block';
    repositionFloat();
  }
  document.addEventListener('click', function(e){
    if (floatEl.style.display === 'block' && !floatEl.contains(e.target)) closeFloat();
  });
  // Follow the anchor button on scroll instead of closing on it. This
  // matters because "scroll" fires (capture-phase, at window) for scrolling
  // ANY descendant - including the value-search list inside the panel
  // itself. Closing on that made the filter panel vanish the instant you
  // tried to scroll through a long value list, which is exactly the list
  // a project with many creators/families/types needs to scroll through.
  // Repositioning is also a no-op (cheap) whenever the anchor hasn't
  // actually moved, which covers scrolling the table body under its own
  // sticky header.
  window.addEventListener('scroll', repositionFloat, true);
  window.addEventListener('resize', repositionFloat);

  // =================================================================
  // DataTable - the one reusable table used by BOTH the Detail tab and
  // the Summary tab: sticky header, per-column filter, quick search,
  // pagination, CSV export, and named filter templates (v1 had two
  // separate, slightly-diverged copies of all of this).
  // =================================================================
  function createDataTable(root, opts) {
    opts = opts || {};
    var storageKey = opts.storageKey || 'dataTable';
    var exportPrefix = opts.exportPrefix || 'export';

    root.innerHTML =
      '<div class="table-toolbar">' +
        '<button data-act="reset">Reinitialiser les filtres</button>' +
        '<button data-act="export">Exporter en Excel</button>' +
        '<button data-act="copyids" title="Copie les ElementId de TOUTES les lignes filtrees (pas seulement la page affichee) - collez dans Revit > Selectionner par ID." style="display:none">Copier les ID</button>' +
        '<div class="quicksearch"><input type="text" data-act="search" placeholder="Recherche rapide..."/></div>' +
        '<div class="grow"></div>' +
        '<div data-role="rowcount"></div>' +
        '<div>Lignes/page: <select data-act="pagesize">' +
          '<option value="50">50</option><option value="100" selected>100</option>' +
          '<option value="250">250</option><option value="500">500</option>' +
          '<option value="999999">Toutes</option>' +
        '</select></div>' +
        '<div id="pager"><button data-act="prev">&laquo;</button> <span data-role="pageinfo"></span> <button data-act="next">&raquo;</button></div>' +
      '</div>' +
      '<div class="table-scroll"><table><colgroup data-role="colgroup"></colgroup><thead><tr data-role="headrow"></tr></thead><tbody data-role="body"></tbody></table></div>';

    var elResetBtn = root.querySelector('[data-act="reset"]');
    var elExportBtn = root.querySelector('[data-act="export"]');
    var elCopyIdsBtn = root.querySelector('[data-act="copyids"]');
    var elSearch = root.querySelector('[data-act="search"]');
    var elRowCount = root.querySelector('[data-role="rowcount"]');
    var elPageSize = root.querySelector('[data-act="pagesize"]');
    var elPrev = root.querySelector('[data-act="prev"]');
    var elNext = root.querySelector('[data-act="next"]');
    var elPageInfo = root.querySelector('[data-role="pageinfo"]');
    var elHeadRow = root.querySelector('[data-role="headrow"]');
    var elBody = root.querySelector('[data-role="body"]');
    var elColgroup = root.querySelector('[data-role="colgroup"]');

    var columns = [], allRows = [], filteredRows = [];
    var activeFilters = [], excludedSets = [];
    // Every distinct value ever seen for each column, from the FULL
    // (unfiltered) row set - used for filter templates, which need to be
    // able to name a value even when it's currently hidden by some other
    // active filter. The filter DROPDOWN itself uses a different,
    // dynamically-recomputed list - see computeAvailableValues() below.
    var columnAllValues = [];
    var currentPage = 0, pageSize = 100, searchText = '';
    var rowClassFn = null, cellRenderFn = null, numGroupCols = 0;
    // Column widths, keyed by NAME (not index) so a resize survives a
    // filter/date change that re-renders the same columns, and a column
    // that's new in this view (e.g. a just-added extra parameter) simply
    // starts at the default instead of inheriting some other column's
    // width by coincidence of position.
    var DEFAULT_COL_WIDTH = 130;
    var colWidthsByName = {};

    function buildColumnValueIndex() {
      columnAllValues = [];
      excludedSets = [];
      activeFilters = [];
      for (var c = 0; c < columns.length; c++) {
        var seen = {};
        for (var r = 0; r < allRows.length; r++) seen[String(allRows[r][c])] = true;
        columnAllValues.push(Object.keys(seen).sort(simpleTextSort));
        excludedSets.push(makeValueSet());
        activeFilters.push(null);
      }
    }

    // The values offered in ONE column's filter dropdown, given every
    // OTHER column's currently active filter (and the quick search box) -
    // this is what makes the checkboxes "faceted": filter TypeSousProjet
    // down to Utilisateur, then open Workset's dropdown, and only the
    // worksets that actually occur on Utilisateur rows are listed. The
    // column's OWN filter is deliberately excluded from this check, so a
    // value you've already excluded from itself still appears (unchecked)
    // and can be re-included.
    function computeAvailableValues(skipColIndex) {
      var seen = {};
      for (var r = 0; r < allRows.length; r++) {
        var row = allRows[r];
        var ok = true;
        for (var c = 0; c < columns.length; c++) {
          if (c === skipColIndex) continue;
          var f = activeFilters[c];
          if (f && f.has(String(row[c]))) { ok = false; break; }
        }
        if (ok && matchesSearch(row)) seen[String(row[skipColIndex])] = true;
      }
      return Object.keys(seen).sort(simpleTextSort);
    }

    function colWidthFor(name) {
      return colWidthsByName[name] || DEFAULT_COL_WIDTH;
    }
    function wireColumnResize(handle, colName, colEl) {
      handle.addEventListener('mousedown', function(e){
        e.preventDefault();
        e.stopPropagation(); // don't also open the filter dropdown
        var startX = e.clientX;
        var startWidth = colWidthFor(colName);
        handle.className = 'col-resize-handle active';
        function onMove(ev) {
          var w = Math.max(46, startWidth + (ev.clientX - startX));
          colWidthsByName[colName] = w;
          colEl.style.width = w + 'px';
        }
        function onUp() {
          handle.className = 'col-resize-handle';
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
      // Double-click a handle to reset that one column back to the default -
      // faster than dragging it back by eye once it's been narrowed a lot.
      handle.addEventListener('dblclick', function(e){
        e.stopPropagation();
        delete colWidthsByName[colName];
        colEl.style.width = DEFAULT_COL_WIDTH + 'px';
      });
    }
    function buildHeader() {
      elHeadRow.innerHTML = '';
      elColgroup.innerHTML = '';
      for (var c = 0; c < columns.length; c++) {
        (function(colIndex){
          var colName = columns[colIndex];
          var colEl = document.createElement('col');
          colEl.style.width = colWidthFor(colName) + 'px';
          elColgroup.appendChild(colEl);

          var th = document.createElement('th');
          th.appendChild(document.createTextNode(colName));
          var btn = document.createElement('span');
          btn.className = 'filter-btn';
          btn.textContent = '\u25BC';
          th.appendChild(btn);
          btn.addEventListener('click', function(e){
            e.stopPropagation();
            var isOpenForThis = floatOwner && floatOwner.table === api && floatOwner.colIndex === colIndex && floatEl.style.display === 'block';
            closeFloat();
            if (!isOpenForThis) openFloatFor(btn, api, colIndex);
          });
          var handle = document.createElement('span');
          handle.className = 'col-resize-handle';
          handle.title = 'Glisser pour redimensionner - double-clic pour reinitialiser';
          th.appendChild(handle);
          wireColumnResize(handle, colName, colEl);
          elHeadRow.appendChild(th);
        })(c);
      }
      updateFilterButtonStates();
    }

    function updateFilterButtonStates() {
      var btns = elHeadRow.querySelectorAll('.filter-btn');
      for (var c = 0; c < btns.length; c++) {
        btns[c].className = 'filter-btn' + (activeFilters[c] ? ' active' : '');
      }
    }

    function renderFilterPanel(colIndex, container) {
      container.innerHTML = '';
      // Recomputed fresh every time the panel opens, so it always reflects
      // whatever other filters (and the quick search) are active right now
      // - see computeAvailableValues() for why.
      var availableValues = computeAvailableValues(colIndex);

      var searchBox = document.createElement('input');
      searchBox.type = 'text';
      searchBox.placeholder = 'Rechercher des valeurs...';
      container.appendChild(searchBox);

      var selectAllLabel = document.createElement('label');
      var selectAllCb = document.createElement('input');
      selectAllCb.type = 'checkbox';
      selectAllCb.checked = availableValues.every(function(v){ return !excludedSets[colIndex].has(v); });
      selectAllLabel.appendChild(selectAllCb);
      selectAllLabel.appendChild(document.createTextNode(' (Tout selectionner)'));
      container.appendChild(selectAllLabel);

      var listDiv = document.createElement('div');
      container.appendChild(listDiv);
      var noteDiv = document.createElement('div');
      noteDiv.className = 'filter-note';
      container.appendChild(noteDiv);
      var otherActive = activeFilters.some(function(f, c){ return c !== colIndex && f; });
      if (otherActive) {
        var scopeNote = document.createElement('div');
        scopeNote.className = 'filter-note';
        scopeNote.textContent = 'Liste limitee aux valeurs presentes avec les autres filtres actifs.';
        container.appendChild(scopeNote);
      }

      function renderList(query) {
        var q = (query || '').toLowerCase();
        var matches = [];
        for (var i = 0; i < availableValues.length; i++) {
          if (q === '' || availableValues[i].toLowerCase().indexOf(q) !== -1) matches.push(availableValues[i]);
        }
        var fragment = document.createDocumentFragment();
        var excluded = excludedSets[colIndex];
        matches.forEach(function(val){
          var label = document.createElement('label');
          var cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.checked = !excluded.has(val);
          cb.addEventListener('change', function(){
            if (cb.checked) excluded.remove(val); else excluded.add(val);
          });
          label.appendChild(cb);
          label.appendChild(document.createTextNode(' ' + (val === '' ? '(vide)' : val)));
          fragment.appendChild(label);
        });
        listDiv.innerHTML = '';
        listDiv.appendChild(fragment);
        noteDiv.textContent = matches.length + ' valeur(s)' + (q !== '' ? ' correspondant a "' + query + '"' : ' au total') +
          ' - ' + availableValues.length + ' uniques avec les filtres actuels.';
      }
      renderList('');
      searchBox.addEventListener('input', debounce(function(){ renderList(searchBox.value); }, 120));
      selectAllCb.addEventListener('change', function(){
        // Only touches values currently on offer - a value hidden right now
        // by some OTHER filter keeps whatever exclusion state it already
        // had, rather than being silently included or excluded by this.
        availableValues.forEach(function(v){
          if (selectAllCb.checked) excludedSets[colIndex].remove(v);
          else excludedSets[colIndex].add(v);
        });
        renderList(searchBox.value);
      });

      var btnRow = document.createElement('div');
      btnRow.className = 'ftbtns';
      var applyBtn = document.createElement('button');
      applyBtn.className = 'primary';
      applyBtn.textContent = 'Appliquer';
      applyBtn.addEventListener('click', function(){
        var n = excludedSets[colIndex].count();
        activeFilters[colIndex] = n > 0 ? excludedSets[colIndex] : null;
        updateFilterButtonStates();
        applyFilters();
        closeFloat();
      });
      var clearBtn = document.createElement('button');
      clearBtn.textContent = 'Effacer';
      clearBtn.addEventListener('click', function(){
        excludedSets[colIndex].clear();
        activeFilters[colIndex] = null;
        updateFilterButtonStates();
        applyFilters();
        closeFloat();
      });
      btnRow.appendChild(applyBtn);
      btnRow.appendChild(clearBtn);
      container.appendChild(btnRow);
      searchBox.focus();
    }

    function matchesSearch(row) {
      if (searchText === '') return true;
      for (var c = 0; c < row.length; c++) {
        if (String(row[c]).toLowerCase().indexOf(searchText) !== -1) return true;
      }
      return false;
    }

    function applyFilters() {
      filteredRows = allRows.filter(function(row){
        for (var c = 0; c < columns.length; c++) {
          var f = activeFilters[c];
          if (f && f.has(String(row[c]))) return false;
        }
        return matchesSearch(row);
      });
      currentPage = 0;
      renderPage();
    }

    function renderPage() {
      var total = filteredRows.length;
      var totalPages = Math.max(1, Math.ceil(total / pageSize));
      if (currentPage >= totalPages) currentPage = totalPages - 1;
      if (currentPage < 0) currentPage = 0;
      var startIdx = currentPage * pageSize;
      var endIdx = Math.min(startIdx + pageSize, total);

      var fragment = document.createDocumentFragment();
      for (var r = startIdx; r < endIdx; r++) {
        var tr = document.createElement('tr');
        var rowData = filteredRows[r];
        if (rowClassFn) {
          var cls = rowClassFn(rowData);
          if (cls) tr.className = cls;
        }
        for (var c = 0; c < columns.length; c++) {
          var td = document.createElement('td');
          if (numGroupCols && c >= numGroupCols) td.className = 'count';
          var handled = cellRenderFn ? cellRenderFn(rowData, c, td) : false;
          if (!handled) td.textContent = rowData[c];
          tr.appendChild(td);
        }
        fragment.appendChild(tr);
      }
      elBody.innerHTML = '';
      elBody.appendChild(fragment);
      elRowCount.textContent = 'Affichage ' + (total === 0 ? 0 : startIdx + 1) + '-' + endIdx + ' sur ' + total +
        (allRows.length !== total ? ' (total non filtre : ' + allRows.length + ')' : '');
      elPageInfo.textContent = 'Page ' + (currentPage + 1) + ' / ' + totalPages;
      elPrev.disabled = currentPage <= 0;
      elNext.disabled = currentPage >= totalPages - 1;
    }

    elPageSize.addEventListener('change', function(){ pageSize = parseInt(elPageSize.value, 10); currentPage = 0; renderPage(); });
    elPrev.addEventListener('click', function(){ currentPage--; renderPage(); });
    elNext.addEventListener('click', function(){ currentPage++; renderPage(); });
    elResetBtn.addEventListener('click', function(){
      for (var c = 0; c < columns.length; c++) { excludedSets[c].clear(); activeFilters[c] = null; }
      elSearch.value = ''; searchText = '';
      updateFilterButtonStates();
      applyFilters();
    });
    elExportBtn.addEventListener('click', function(){ exportRowsToExcel(columns, filteredRows, exportPrefix); });
    elCopyIdsBtn.addEventListener('click', function(){
      var idIdx = columns.indexOf('ElementId');
      if (idIdx === -1) return; // button is hidden whenever there's no ElementId column, but guard anyway
      if (filteredRows.length === 0) { alert('Aucun element dans le filtre actuel.'); return; }
      var ids = filteredRows.map(function(row){ return row[idIdx]; });
      var text = ids.join(',');
      copyTextToClipboard(text, function(ok){
        var original = elCopyIdsBtn.textContent;
        if (ok) {
          elCopyIdsBtn.textContent = 'Copie ! (' + ids.length + ' ID)';
        } else {
          // Automatic clipboard access can be refused by the browser (old
          // WebView, blocked permission, etc.) - fall back to a prompt()
          // dialog with the text pre-selected so the user can still grab it
          // with Ctrl+C instead of losing the list entirely.
          elCopyIdsBtn.textContent = 'Copie manuelle requise';
          window.prompt('Copie automatique impossible. Selectionnez le texte ci-dessous et faites Ctrl+C :', text);
        }
        elCopyIdsBtn.disabled = true;
        setTimeout(function(){ elCopyIdsBtn.textContent = original; elCopyIdsBtn.disabled = false; }, 1600);
      });
    });
    elSearch.addEventListener('input', debounce(function(){
      searchText = elSearch.value.toLowerCase();
      applyFilters();
    }, 150));

    var api = {
      setData: function(newColumns, newRows, config) {
        config = config || {};
        columns = newColumns;
        allRows = newRows;
        numGroupCols = config.numGroupCols || 0;
        rowClassFn = config.rowClassFn || null;
        cellRenderFn = config.cellRenderFn || null;
        // "Copy IDs" only makes sense on a per-element view (Detail tab).
        // The Summary tab's rows are aggregated groups with no ElementId
        // column at all, so the button simply doesn't appear there.
        elCopyIdsBtn.style.display = columns.indexOf('ElementId') !== -1 ? '' : 'none';
        buildColumnValueIndex();
        if (config.defaultExcludeByColumn) {
          for (var colName in config.defaultExcludeByColumn) {
            var idx = columns.indexOf(colName);
            if (idx !== -1) {
              var keepSet = {};
              config.defaultExcludeByColumn[colName].forEach(function(v){ keepSet[v] = true; });
              var ex = makeValueSet();
              columnAllValues[idx].forEach(function(v){ if (!keepSet[v]) ex.add(v); });
              if (ex.count() > 0) { excludedSets[idx] = ex; activeFilters[idx] = ex; }
            }
          }
        }
        buildHeader();
        applyFilters();
      },
      getColumnValues: function(colIndex) { return columnAllValues[colIndex]; },
      renderFilterPanel: renderFilterPanel,
      getFilteredRows: function() { return filteredRows; },
      captureFilters: function() {
        var tmpl = {};
        for (var c = 0; c < columns.length; c++) {
          var f = activeFilters[c];
          if (f) {
            var vals = [];
            columnAllValues[c].forEach(function(v){ if (f.has(v)) vals.push(v); });
            tmpl[columns[c]] = vals;
          }
        }
        return tmpl;
      },
      applyFilterTemplate: function(tmpl) {
        for (var c = 0; c < columns.length; c++) {
          var colName = columns[c];
          if (Object.prototype.hasOwnProperty.call(tmpl, colName)) {
            excludedSets[c] = makeValueSet(tmpl[colName]);
            activeFilters[c] = excludedSets[c].count() > 0 ? excludedSets[c] : null;
          } else {
            excludedSets[c].clear();
            activeFilters[c] = null;
          }
        }
        updateFilterButtonStates();
        applyFilters();
      }
    };
    return api;
  }

  // Named filter/layout template storage, shared shape for both tabs
  // (Detail stores column-filter sets; Summary stores full layout configs).
  function makeTemplateStore(storageKey) {
    return {
      load: function() { return Store.get(storageKey, {}); },
      save: function(name, value) {
        var all = Store.get(storageKey, {});
        all[name] = value;
        Store.set(storageKey, all);
      },
      remove: function(name) {
        var all = Store.get(storageKey, {});
        delete all[name];
        Store.set(storageKey, all);
      }
    };
  }
  function wireTemplateUI(prefix, store, onApply, captureFn) {
    var nameInput = document.getElementById(prefix + 'Name');
    var saveBtn = document.getElementById(prefix + 'SaveBtn');
    var select = document.getElementById(prefix + 'Select');
    var applyBtn = document.getElementById(prefix + 'ApplyBtn');
    var deleteBtn = document.getElementById(prefix + 'DeleteBtn');
    function refresh() {
      var all = store.load();
      select.innerHTML = '<option value="">-- Choisir --</option>';
      Object.keys(all).sort().forEach(function(name){
        var o = document.createElement('option'); o.value = name; o.textContent = name; select.appendChild(o);
      });
    }
    saveBtn.addEventListener('click', function(){
      var name = (nameInput.value || '').trim();
      if (!name) { alert('Donnez un nom.'); return; }
      store.save(name, captureFn());
      refresh();
      select.value = name;
    });
    applyBtn.addEventListener('click', function(){
      var name = select.value;
      if (!name) { alert('Choisissez un gabarit.'); return; }
      var all = store.load();
      if (all[name]) onApply(all[name]);
    });
    deleteBtn.addEventListener('click', function(){
      var name = select.value;
      if (!name) { alert('Choisissez un gabarit a supprimer.'); return; }
      store.remove(name);
      refresh();
    });
    refresh();
  }

  // =================================================================
  // Chart primitives (Diagrams tab) - plain SVG strings, no library.
  // One hue per chart when bars encode the SAME measure across different
  // labels (color would only encode rank, which the palette's own rule
  // forbids); a fixed categorical slot per entity when color itself
  // carries identity (the CategoryType donut). Every mark carries a
  // native <title> child, which is a real (if minimal) hover tooltip
  // with zero extra markup.
  // =================================================================
  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function truncateLabel(s, max) {
    s = String(s);
    return s.length > max ? s.slice(0, max - 1) + '\u2026' : s;
  }

  function barChartSVG(data, opts) {
    opts = opts || {};
    var color = opts.color || 'var(--series-1)';
    var W = 720, labelW = opts.labelWidth || 168, rightPad = 54;
    var rowH = 22, gapY = 9;
    var chartW = W - labelW - rightPad;
    var maxV = 1;
    for (var i = 0; i < data.length; i++) if (data[i].value > maxV) maxV = data[i].value;
    var H = data.length * (rowH + gapY) + gapY;
    var parts = [];
    parts.push('<svg class="viz-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img">');
    for (var j = 0; j < data.length; j++) {
      var d = data[j];
      var y = gapY + j * (rowH + gapY);
      var w = Math.max(3, (d.value / maxV) * chartW);
      parts.push('<text x="' + (labelW - 8) + '" y="' + (y + rowH * 0.68).toFixed(1) + '" text-anchor="end" class="viz-catlabel">' + escHtml(truncateLabel(d.label, 22)) + '</text>');
      parts.push('<rect x="' + labelW + '" y="' + y + '" width="' + w.toFixed(1) + '" height="' + rowH + '" rx="3" fill="' + color + '"><title>' + escHtml(d.label) + ': ' + d.value + '</title></rect>');
      parts.push('<text x="' + (labelW + w + 6).toFixed(1) + '" y="' + (y + rowH * 0.68).toFixed(1) + '" class="viz-vallabel">' + d.value + '</text>');
    }
    parts.push('</svg>');
    return parts.join('');
  }

  function donutChartSVG(data) {
    var size = 190, cx = size / 2, cy = size / 2, rOuter = size / 2 - 6, rInner = rOuter * 0.58;
    var total = 0;
    for (var i = 0; i < data.length; i++) total += data[i].value;
    if (total <= 0) total = 1;
    var parts = [];
    parts.push('<svg class="viz-svg viz-donut" viewBox="0 0 ' + size + ' ' + size + '" role="img">');
    var angle = -Math.PI / 2;
    for (var j = 0; j < data.length; j++) {
      var d = data[j];
      var frac = d.value / total;
      var a0 = angle, a1 = angle + frac * 2 * Math.PI;
      angle = a1;
      var largeArc = (a1 - a0) > Math.PI ? 1 : 0;
      var x0o = cx + rOuter * Math.cos(a0), y0o = cy + rOuter * Math.sin(a0);
      var x1o = cx + rOuter * Math.cos(a1), y1o = cy + rOuter * Math.sin(a1);
      var x0i = cx + rInner * Math.cos(a1), y0i = cy + rInner * Math.sin(a1);
      var x1i = cx + rInner * Math.cos(a0), y1i = cy + rInner * Math.sin(a0);
      var path = 'M' + x0o.toFixed(2) + ',' + y0o.toFixed(2) +
        ' A' + rOuter.toFixed(2) + ',' + rOuter.toFixed(2) + ' 0 ' + largeArc + ' 1 ' + x1o.toFixed(2) + ',' + y1o.toFixed(2) +
        ' L' + x0i.toFixed(2) + ',' + y0i.toFixed(2) +
        ' A' + rInner.toFixed(2) + ',' + rInner.toFixed(2) + ' 0 ' + largeArc + ' 0 ' + x1i.toFixed(2) + ',' + y1i.toFixed(2) + ' Z';
      var pct = Math.round(frac * 100);
      parts.push('<path d="' + path + '" fill="' + d.color + '" stroke="var(--panel)" stroke-width="2"><title>' + escHtml(d.label) + ': ' + d.value + ' (' + pct + '%)</title></path>');
    }
    parts.push('</svg>');
    return parts.join('');
  }

  // Multi-series line chart - one line per entity (e.g. per creator), all
  // sharing one axis/scale. Legend is the caller's job (a fixed-order swatch
  // list next to the chart), since color here means identity (a specific
  // person), not magnitude - never resolved by the chart itself picking hues.
  function multiLineChartSVG(series, xLabels) {
    var W = 720, H = 240, padL = 44, padR = 16, padT = 14, padB = 34;
    var chartW = W - padL - padR, chartH = H - padT - padB;
    var n = xLabels.length;
    var maxV = 1;
    series.forEach(function(s){ s.values.forEach(function(v){ if (v > maxV) maxV = v; }); });
    function px(i) { return padL + (n <= 1 ? 0 : (i / (n - 1)) * chartW); }
    function py(v) { return padT + chartH - (v / maxV) * chartH; }
    var parts = [];
    parts.push('<svg class="viz-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img">');
    for (var g = 0; g <= 2; g++) {
      var gy = padT + chartH * (g / 2);
      parts.push('<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '" class="viz-gridline"/>');
    }
    series.forEach(function(s){
      var d = '';
      for (var i = 0; i < n; i++) d += (i === 0 ? 'M' : 'L') + px(i).toFixed(1) + ',' + py(s.values[i]).toFixed(1) + ' ';
      parts.push('<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>');
      for (var j = 0; j < n; j++) {
        parts.push('<circle cx="' + px(j).toFixed(1) + '" cy="' + py(s.values[j]).toFixed(1) + '" r="3.5" fill="' + s.color + '" stroke="var(--panel)" stroke-width="1.2"><title>' + escHtml(s.label) + ' - ' + escHtml(xLabels[j]) + ': ' + s.values[j] + '</title></circle>');
      }
    });
    parts.push('<text x="' + padL + '" y="' + (H - 8) + '" class="viz-axislabel" text-anchor="start">' + escHtml(xLabels[0]) + '</text>');
    if (n > 1) parts.push('<text x="' + (W - padR) + '" y="' + (H - 8) + '" class="viz-axislabel" text-anchor="end">' + escHtml(xLabels[n - 1]) + '</text>');
    parts.push('<text x="' + (padL - 6) + '" y="' + (padT + 4) + '" class="viz-axislabel" text-anchor="end">' + maxV + '</text>');
    parts.push('<text x="' + (padL - 6) + '" y="' + (padT + chartH) + '" class="viz-axislabel" text-anchor="end">0</text>');
    parts.push('</svg>');
    return parts.join('');
  }

  // =================================================================
  // DETAIL TAB - per-element view: pick a date, or compare two dates
  // to see Added / Deleted / Modified elements. Called once from
  // bootApp() after the report folder is picked and the latest
  // snapshot is preloaded - see the onboarding wiring at the bottom of
  // this file.
  // =================================================================
  function setupDetailTab(){
    var latestKey = ReportStore.latestKey();

    var dateShow = document.getElementById('d_dateShow');
    var dateA = document.getElementById('d_dateA');
    var dateB = document.getElementById('d_dateB');
    var subTitle = document.getElementById('d_subTitle');

    // Re-run after the history folder is loaded so the older dates appear
    // in the pickers (before that, only the current run's date is known).
    function fillDateSelects() {
      var keys = ReportStore.dateKeys();
      [dateShow, dateA, dateB].forEach(function(sel){
        var cur = sel.value;
        sel.innerHTML = '';
        keys.forEach(function(k){
          var o = document.createElement('option'); o.value = k; o.textContent = k; sel.appendChild(o);
        });
        if (cur && keys.indexOf(cur) !== -1) sel.value = cur;
      });
      if (!dateShow.value) dateShow.value = ReportStore.latestKey();
      if (keys.length >= 2) { dateA.value = keys[keys.length - 2]; dateB.value = keys[keys.length - 1]; }
    }
    fillDateSelects();
    onAfterFolder(function(){ fillDateSelects(); refillParamSelect(); });

    var table = createDataTable(document.getElementById('d_table'), {storageKey: 'detailTable', exportPrefix: 'element_report'});

    // ---- "Add parameters" - the base columns above never change; this
    // just appends extra columns on demand, resolved from the parameter
    // change-log Python embedded (see the v2 notes near ParamsDelta_ in
    // the generator script). Nothing here alters showDate/compareDates'
    // own column sets - it only extends whatever they already produced.
    var paramSelect = document.getElementById('d_paramSelect');
    function refillParamSelect() {
      var have = {};
      for (var i = 0; i < paramSelect.options.length; i++) have[paramSelect.options[i].value] = true;
      ReportStore.getAvailableParams().forEach(function(p){
        if (!have[p]) { var o = document.createElement('option'); o.value = p; o.textContent = p; paramSelect.appendChild(o); }
      });
    }
    refillParamSelect();
    var extraParams = [];
    var resolvedParamsCache = {};

    function getResolvedParamsAt(dateKey) {
      if (resolvedParamsCache[dateKey]) return resolvedParamsCache[dateKey];
      var state = {};
      var deltas = ReportStore.getDeltas();
      for (var i = 0; i < deltas.length; i++) {
        if (deltas[i].date > dateKey) break; // deltas are date-sorted; "YYYY-MM-DD HH:MM:SS" sorts chronologically as text
        var changes = deltas[i].changes;
        for (var eid in changes) {
          if (!Object.prototype.hasOwnProperty.call(changes, eid)) continue;
          if (!state[eid]) state[eid] = {};
          var pmap = changes[eid];
          for (var pn in pmap) { if (Object.prototype.hasOwnProperty.call(pmap, pn)) state[eid][pn] = pmap[pn]; }
        }
      }
      resolvedParamsCache[dateKey] = state;
      return state;
    }
    function augmentWithExtraParams(cols, rows, dateForRowFn) {
      if (extraParams.length === 0) return {columns: cols, rows: rows};
      var newCols = cols.concat(extraParams);
      var newRows = rows.map(function(row){
        var eid = String(row[0]);
        var resolved = getResolvedParamsAt(dateForRowFn(row));
        var extra = extraParams.map(function(pn){
          return (resolved[eid] && resolved[eid][pn] !== undefined) ? resolved[eid][pn] : '';
        });
        return row.concat(extra);
      });
      return {columns: newCols, rows: newRows};
    }
    function renderParamChips() {
      var host = document.getElementById('d_paramChips');
      host.innerHTML = extraParams.map(function(p, i){
        return '<span class="chip">' + escHtml(p) + ' <button type="button" class="chip-x" data-i="' + i + '">&times;</button></span>';
      }).join('');
      var buttons = host.querySelectorAll('.chip-x');
      for (var i = 0; i < buttons.length; i++) {
        buttons[i].addEventListener('click', function(e){
          extraParams.splice(parseInt(e.target.getAttribute('data-i'), 10), 1);
          renderParamChips();
          rerenderCurrentView();
        });
      }
    }
    function addChosenParam() {
      var p = paramSelect.value;
      if (!p) { alert('Choisissez d\'abord un parametre dans la liste.'); return; }
      if (extraParams.indexOf(p) !== -1) return; // already shown
      extraParams.push(p);
      renderParamChips();
      rerenderCurrentView();
    }
    document.getElementById('d_paramAddBtn').addEventListener('click', function(){
      // The parameter NAMES are known from the inline snapshot, but their
      // VALUES per element come from the change-log files in the export
      // folder - so adding a column needs the folder loaded. Load it once
      // here, then add straight away (no second click).
      if (!ReportStore.hasFolder()) {
        var btn = document.getElementById('d_paramAddBtn');
        btn.disabled = true; var t = btn.textContent; btn.textContent = 'Chargement...';
        promptForFolder().then(function(){
          btn.disabled = false; btn.textContent = t;
          refillParamSelect();
          addChosenParam();
        }, function(){
          btn.disabled = false; btn.textContent = t;
        });
        return;
      }
      addChosenParam();
    });

    function badgeCell(rowData, colIndex, td, changeColIdx) {
      if (colIndex !== changeColIdx) return false;
      var badgeText = {Added:'Ajoute', Deleted:'Supprime', Modified:'Modifie'};
      var badgeClass = {Added:'badge-added', Deleted:'badge-deleted', Modified:'badge-modified'};
      var v = rowData[colIndex];
      if (!badgeText[v]) return false;
      var span = document.createElement('span');
      span.className = 'badge ' + badgeClass[v];
      span.textContent = badgeText[v];
      td.appendChild(span);
      return true;
    }

    function showLatest() { showDate(latestKey); dateShow.value = latestKey; }

    // Same default as the Recapitulatif tab: only Utilisateur worksets on
    // first load (view/family worksets are mostly Revit-internal noise).
    // Only the FIRST render gets this - once you've touched the column
    // filters yourself (or switched dates), your own choice is never
    // silently overwritten back to the default.
    var detailFirstLoad = true;
    var lastMode = 'date', lastDateKey = null, lastCompareA = null, lastCompareB = null;
    // Only the latest date is guaranteed already loaded (preloaded by
    // bootApp() before this tab is even set up) - any other date is
    // read from its own CSV on first request here, then cached by
    // ReportStore so switching back to it later is instant.
    function showDate(key) {
      lastMode = 'date'; lastDateKey = key;
      subTitle.textContent = 'Chargement de ' + key + '...';
      ReportStore.getSnapshot(key).then(function(snap){
        if (lastMode !== 'date' || lastDateKey !== key) return; // superseded by a newer request
        subTitle.textContent = 'Date affichee : ' + key;
        var defaultExclude = null;
        if (detailFirstLoad) {
          detailFirstLoad = false;
          if (snap.columns.indexOf('TypeSousProjet') !== -1) defaultExclude = {TypeSousProjet: ['Utilisateur']};
        }
        var aug = augmentWithExtraParams(snap.columns, snap.rows, function(){ return key; });
        table.setData(aug.columns, aug.rows, {defaultExcludeByColumn: defaultExclude});
      }, function(){
        subTitle.textContent = 'Erreur de lecture de l\'instantane ' + key + '.';
      });
    }
    function rerenderCurrentView() {
      if (lastMode === 'compare' && lastCompareA && lastCompareB) compareDates(lastCompareA, lastCompareB);
      else if (lastDateKey) showDate(lastDateKey);
    }

    function diffFields(before, after, header) {
      var changed = [];
      for (var i = 1; i < header.length; i++) {
        if (String(before[i]) !== String(after[i])) changed.push(header[i]);
      }
      return changed;
    }

    function compareDates(keyA, keyB) {
      lastMode = 'compare'; lastCompareA = keyA; lastCompareB = keyB;
      var beforeKey = keyA < keyB ? keyA : keyB;
      var afterKey = keyA < keyB ? keyB : keyA;
      subTitle.textContent = 'Chargement de ' + beforeKey + ' et ' + afterKey + '...';
      Promise.all([ReportStore.getSnapshot(beforeKey), ReportStore.getSnapshot(afterKey)]).then(function(pair){
        if (lastMode !== 'compare' || lastCompareA !== keyA || lastCompareB !== keyB) return; // superseded
        var before = pair[0], after = pair[1];
        var header = before.columns;

        var idLCB = header.indexOf('LastChangedBy');
        if (idLCB === -1) idLCB = header.length - 1;

        var attrIdx = [];
        for (var i = 1; i < header.length; i++) { if (i !== idLCB) attrIdx.push(i); }
        var attrLabels = attrIdx.map(function(i){ return header[i]; });
        function attrVals(row) { return attrIdx.map(function(i){ return row[i]; }); }

        var beforeMap = {}; before.rows.forEach(function(r){ beforeMap[r[0]] = r; });
        var afterMap = {}; after.rows.forEach(function(r){ afterMap[r[0]] = r; });

        var diffHeader = ['ElementId', 'ChangeType', 'ChangedFields'].concat(attrLabels).concat(['ModifiePar_Avant', 'ModifiePar_Apres']);
        var diffRows = [];

        Object.keys(afterMap).forEach(function(eid){
          var a = afterMap[eid];
          if (!(eid in beforeMap)) {
            diffRows.push([eid, 'Added', ''].concat(attrVals(a)).concat(['', a[idLCB]]));
          } else {
            var b = beforeMap[eid];
            var changed = diffFields(b, a, header);
            if (changed.length > 0) {
              diffRows.push([eid, 'Modified', changed.join(', ')].concat(attrVals(a)).concat([b[idLCB], a[idLCB]]));
            }
          }
        });
        Object.keys(beforeMap).forEach(function(eid){
          if (!(eid in afterMap)) {
            var b = beforeMap[eid];
            diffRows.push([eid, 'Deleted', ''].concat(attrVals(b)).concat([b[idLCB], '']));
          }
        });

        var changeColIdx = diffHeader.indexOf('ChangeType');
        subTitle.textContent = diffRows.length + ' element(s) change(s) entre ' + beforeKey + ' et ' + afterKey +
          '. Pour "Modifie": ModifiePar_Apres = qui a fait le changement. Pour "Supprime": ModifiePar_Avant = dernier ' +
          'a l\'avoir modifie avant suppression (l\'auteur exact de la suppression n\'est pas disponible via comparaison de snapshots).';
        var aug = augmentWithExtraParams(diffHeader, diffRows, function(row){
          return row[changeColIdx] === 'Deleted' ? beforeKey : afterKey;
        });
        table.setData(aug.columns, aug.rows, {
          rowClassFn: function(row){
            var v = row[changeColIdx];
            return v === 'Added' ? 'diff-added' : v === 'Deleted' ? 'diff-deleted' : v === 'Modified' ? 'diff-modified' : '';
          },
          cellRenderFn: function(row, c, td){ return badgeCell(row, c, td, changeColIdx); }
        });
      }, function(){
        subTitle.textContent = 'Erreur de lecture des instantanes ' + beforeKey + ' / ' + afterKey + '.';
      });
    }

    document.getElementById('d_showBtn').addEventListener('click', function(){
      if (dateShow.value !== ReportStore.latestKey() && !ReportStore.hasFolder()) {
        promptForFolder().then(function(){ fillDateSelects(); showDate(dateShow.value); }, function(){});
        return;
      }
      showDate(dateShow.value);
    });
    function doCompare() {
      if (dateA.value === dateB.value) { alert('Choisissez deux dates differentes a comparer.'); return; }
      compareDates(dateA.value, dateB.value);
    }
    document.getElementById('d_compareBtn').addEventListener('click', function(){
      if (!ReportStore.hasFolder()) {
        promptForFolder().then(function(){ fillDateSelects(); doCompare(); }, function(){});
        return;
      }
      doCompare();
    });
    document.getElementById('d_backBtn').addEventListener('click', showLatest);

    wireTemplateUI('d_tmpl', makeTemplateStore('revitFilterTemplates_report'),
      function(tmpl){ table.applyFilterTemplate(tmpl); },
      function(){ return table.captureFilters(); });

    showLatest();
  }

  // =================================================================
  // SUMMARY TAB - grouped counts (by workset / category / etc.), with
  // an optional CategoryType pivot and an aggregate compare mode.
  // =================================================================
  function setupSummaryTab(){
    var latestKey = ReportStore.latestKey();
    var dateKeys = ReportStore.dateKeys();
    var latest = ReportStore.getCached(latestKey); // preloaded by bootApp()
    var snapCols = latest.columns;

    var groupable = snapCols.filter(function(c){ return c !== 'ElementId'; });
    var DEFAULT_ORDER = ['Workset','CategoryType','TypeSousProjet','Type','Family','Name','Category','Creator','LastChangedBy'];
    var groupOrder = DEFAULT_ORDER.filter(function(c){ return groupable.indexOf(c) !== -1; });
    groupable.forEach(function(c){ if (groupOrder.indexOf(c) === -1) groupOrder.push(c); });
    var groupChecked = {};
    groupable.forEach(function(c){ groupChecked[c] = (c === 'Workset' || c === 'CategoryType'); });

    var dateShow = document.getElementById('s_dateShow');
    var wsKindFilter = document.getElementById('s_wsKindFilter');
    var groupColsListEl = document.getElementById('s_groupColsList');
    var pivotCatEl = document.getElementById('s_pivotCat');
    var catValuesEl = document.getElementById('s_catValues');
    var subTitle = document.getElementById('s_subTitle');
    var personField = document.getElementById('s_personField');
    var personFilter = document.getElementById('s_personFilter');
    var cntElements = document.getElementById('s_cntElements');
    var cntTypes = document.getElementById('s_cntTypes');
    var cntFamilies = document.getElementById('s_cntFamilies');
    var cmpDateA = document.getElementById('s_cmpDateA');
    var cmpDateB = document.getElementById('s_cmpDateB');

    function fillDateSelects() {
      var keys = ReportStore.dateKeys();
      [dateShow, cmpDateA, cmpDateB].forEach(function(sel){
        var cur = sel.value;
        sel.innerHTML = '';
        keys.forEach(function(k){
          var o = document.createElement('option'); o.value = k; o.textContent = k; sel.appendChild(o);
        });
        if (cur && keys.indexOf(cur) !== -1) sel.value = cur;
      });
      if (!dateShow.value) dateShow.value = ReportStore.latestKey();
      if (keys.length >= 2) { cmpDateA.value = keys[keys.length - 2]; cmpDateB.value = keys[keys.length - 1]; }
    }
    fillDateSelects();
    onAfterFolder(fillDateSelects);

    // Scoped to the latest snapshot only (not every historical run) so
    // opening this tab stays instant regardless of how many exports have
    // piled up - see the ReportStore comment at the top of this file. A
    // person who only ever appears in older snapshots won't be offered
    // here, but their rows are still shown correctly if you view or
    // compare that specific older date directly.
    (function fillPersonFilter(){
      var creatorIdx = snapCols.indexOf('Creator');
      var lastIdx = snapCols.indexOf('LastChangedBy');
      var seen = {};
      var rws = latest.rows;
      for (var r = 0; r < rws.length; r++) {
        if (creatorIdx >= 0) { var cv = String(rws[r][creatorIdx]); if (cv) seen[cv] = true; }
        if (lastIdx >= 0) { var lv = String(rws[r][lastIdx]); if (lv) seen[lv] = true; }
      }
      Object.keys(seen).sort().forEach(function(n){
        var o = document.createElement('option'); o.value = n; o.textContent = n; personFilter.appendChild(o);
      });
    })();

    var catTypeValues = [], catChecked = {};
    (function fillCatValues(){
      var ctIdx = snapCols.indexOf('CategoryType');
      if (ctIdx === -1) { pivotCatEl.checked = false; pivotCatEl.disabled = true; return; }
      var seen = {};
      var rws = latest.rows;
      for (var r = 0; r < rws.length; r++) seen[String(rws[r][ctIdx])] = true;
      catTypeValues = Object.keys(seen).sort();
      catTypeValues.forEach(function(v){
        catChecked[v] = (v === 'Model' || v === 'Annotation');
        var lbl = document.createElement('label');
        lbl.style.marginRight = '10px'; lbl.style.display = 'inline-block';
        var cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = v; cb.checked = catChecked[v];
        cb.addEventListener('change', function(){ catChecked[v] = cb.checked; });
        lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + v));
        catValuesEl.appendChild(lbl);
      });
    })();
    function getSelectedCatValues() { return catTypeValues.filter(function(v){ return catChecked[v]; }); }

    function renderGroupList() {
      groupColsListEl.innerHTML = '';
      groupOrder.forEach(function(col, idx){
        var row = document.createElement('div'); row.className = 'gc-row';
        var up = document.createElement('button'); up.textContent = '\u2191'; up.title = 'Monter';
        up.addEventListener('click', function(){
          if (idx > 0) { var t = groupOrder[idx-1]; groupOrder[idx-1] = groupOrder[idx]; groupOrder[idx] = t; renderGroupList(); }
        });
        var down = document.createElement('button'); down.textContent = '\u2193'; down.title = 'Descendre';
        down.addEventListener('click', function(){
          if (idx < groupOrder.length - 1) { var t = groupOrder[idx+1]; groupOrder[idx+1] = groupOrder[idx]; groupOrder[idx] = t; renderGroupList(); }
        });
        var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!groupChecked[col];
        cb.addEventListener('change', function(){ groupChecked[col] = cb.checked; });
        var lbl = document.createElement('span'); lbl.className = 'lbl'; lbl.textContent = col;
        row.appendChild(up); row.appendChild(down); row.appendChild(cb); row.appendChild(lbl);
        groupColsListEl.appendChild(row);
      });
    }
    renderGroupList();

    function getSelectedGroupCols() {
      var sel = groupOrder.filter(function(c){ return groupChecked[c]; });
      return sel.length ? sel : ['Category'];
    }
    function distinctCount(o) { var n = 0; for (var k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) n++; } return n; }
    function metricShort(m) { return m === 'elements' ? 'Nb' : (m === 'types' ? 'Types' : 'Familles'); }

    // aggregate(): identical grouping/pivot semantics to v1 - only the
    // surrounding table/filter/paging shell around this changed. Takes an
    // already-loaded snapshot object (not a date key) - the caller is
    // responsible for fetching it via ReportStore first, since which
    // dates are already in memory varies (see computeSummary/compareSummary).
    function aggregate(snap) {
      var pivot = pivotCatEl.checked && !pivotCatEl.disabled;
      var groupCols = getSelectedGroupCols();
      if (pivot) {
        groupCols = groupCols.filter(function(c){ return c !== 'CategoryType'; });
        if (groupCols.length === 0) groupCols = ['Workset'];
      }
      var wantElements = cntElements.checked, wantTypes = cntTypes.checked, wantFamilies = cntFamilies.checked;
      if (!wantElements && !wantTypes && !wantFamilies) wantElements = true;

      var gIdx = groupCols.map(function(c){ return snap.columns.indexOf(c); });
      var typeIdx = snap.columns.indexOf('Type');
      var familyIdx = snap.columns.indexOf('Family');
      var ctIdx = snap.columns.indexOf('CategoryType');
      var wsKindIdx = snap.columns.indexOf('TypeSousProjet');
      var wsKindSel = wsKindFilter.value;
      var creatorIdx = snap.columns.indexOf('Creator');
      var lastChIdx = snap.columns.indexOf('LastChangedBy');
      var personSel = personFilter.value;
      var personFieldVal = personField.value;
      function personMatch(row) {
        if (personSel === '__ALL__') return true;
        var okC = (creatorIdx >= 0) && (String(row[creatorIdx]) === personSel);
        var okL = (lastChIdx >= 0) && (String(row[lastChIdx]) === personSel);
        if (personFieldVal === 'Creator') return okC;
        if (personFieldVal === 'LastChangedBy') return okL;
        return okC || okL;
      }

      var metrics = [];
      if (wantElements) metrics.push('elements');
      if (wantTypes) metrics.push('types');
      if (wantFamilies) metrics.push('families');

      var groups = {}, order = [], kept = 0, countLabels = [], sortIdx = groupCols.length;

      if (pivot) {
        var catVals = getSelectedCatValues();
        if (catVals.length === 0) catVals = ['Model', 'Annotation'];
        for (var r = 0; r < snap.rows.length; r++) {
          var row = snap.rows[r];
          if (wsKindSel !== '__ALL__' && wsKindIdx >= 0 && String(row[wsKindIdx]) !== wsKindSel) continue;
          if (!personMatch(row)) continue;
          var cv = ctIdx >= 0 ? String(row[ctIdx]) : '';
          if (catVals.indexOf(cv) === -1) continue;
          kept++;
          var keyParts = gIdx.map(function(gi){ return String(gi >= 0 ? row[gi] : ''); });
          var key = keyParts.join('\u0001');
          if (!Object.prototype.hasOwnProperty.call(groups, '$' + key)) {
            var per = {}; catVals.forEach(function(c){ per[c] = {count:0, types:{}, families:{}}; });
            groups['$' + key] = {vals: keyParts, per: per, total: {count:0, types:{}, families:{}}};
            order.push(key);
          }
          var grp = groups['$' + key], cell = grp.per[cv];
          cell.count++;
          if (typeIdx >= 0) cell.types[String(row[typeIdx])] = true;
          if (familyIdx >= 0) cell.families[String(row[familyIdx])] = true;
          grp.total.count++;
          if (typeIdx >= 0) grp.total.types[String(row[typeIdx])] = true;
          if (familyIdx >= 0) grp.total.families[String(row[familyIdx])] = true;
        }
        var single = (metrics.length === 1);
        catVals.forEach(function(cv){ metrics.forEach(function(m){ countLabels.push(single ? cv : (cv + ' - ' + metricShort(m))); }); });
        sortIdx = groupCols.length + countLabels.length;
        countLabels.push('Total elements'); countLabels.push('Nb Types distincts'); countLabels.push('Nb Familles distinctes');
        var byKey = {};
        for (var i = 0; i < order.length; i++) {
          var gobj = groups['$' + order[i]], counts = [];
          catVals.forEach(function(cv2){
            var cell2 = gobj.per[cv2];
            metrics.forEach(function(m){
              if (m === 'elements') counts.push(cell2.count);
              else if (m === 'types') counts.push(distinctCount(cell2.types));
              else counts.push(distinctCount(cell2.families));
            });
          });
          counts.push(gobj.total.count); counts.push(distinctCount(gobj.total.types)); counts.push(distinctCount(gobj.total.families));
          byKey[order[i]] = {vals: gobj.vals, counts: counts};
        }
        return {groupCols: groupCols, countLabels: countLabels, numGroupCols: groupCols.length, keyOrder: order,
                byKey: byKey, sortIdx: sortIdx, kept: kept, wsKindSel: wsKindSel, pivot: true, catVals: catVals};
      }

      for (var r2 = 0; r2 < snap.rows.length; r2++) {
        var row2 = snap.rows[r2];
        if (wsKindSel !== '__ALL__' && wsKindIdx >= 0 && String(row2[wsKindIdx]) !== wsKindSel) continue;
        if (!personMatch(row2)) continue;
        kept++;
        var keyParts2 = gIdx.map(function(gi){ return String(gi >= 0 ? row2[gi] : ''); });
        var key2 = keyParts2.join('\u0001');
        if (!Object.prototype.hasOwnProperty.call(groups, '$' + key2)) { groups['$' + key2] = {vals: keyParts2, count:0, types:{}, families:{}}; order.push(key2); }
        var gobj2 = groups['$' + key2]; gobj2.count++;
        if (typeIdx >= 0) gobj2.types[String(row2[typeIdx])] = true;
        if (familyIdx >= 0) gobj2.families[String(row2[familyIdx])] = true;
      }
      if (wantElements) countLabels.push('Nb elements');
      if (wantTypes) countLabels.push('Nb Types distincts');
      if (wantFamilies) countLabels.push('Nb Familles distinctes');
      var byKey2 = {};
      for (var j = 0; j < order.length; j++) {
        var gobj3 = groups['$' + order[j]], counts2 = [];
        if (wantElements) counts2.push(gobj3.count);
        if (wantTypes) counts2.push(distinctCount(gobj3.types));
        if (wantFamilies) counts2.push(distinctCount(gobj3.families));
        byKey2[order[j]] = {vals: gobj3.vals, counts: counts2};
      }
      return {groupCols: groupCols, countLabels: countLabels, numGroupCols: groupCols.length, keyOrder: order,
              byKey: byKey2, sortIdx: groupCols.length, kept: kept, wsKindSel: wsKindSel, pivot: false};
    }

    var table = createDataTable(document.getElementById('s_table'), {storageKey: 'summaryTable', exportPrefix: 'element_summary'});
    var cmpState = null;
    var firstLoad = true;
    var computeToken = 0; // guards against a slow load finishing after a newer request started

    function deltaCellRender(row, c, td, firstDeltaIdx) {
      if (c < firstDeltaIdx) return false;
      var dv = Number(row[c]);
      td.textContent = (dv > 0 ? '+' : '') + row[c];
      if (dv > 0) td.style.color = 'var(--added)';
      else if (dv < 0) td.style.color = 'var(--deleted)';
      return true;
    }

    // Only the latest date is guaranteed already loaded - any other date
    // is fetched from its own CSV on demand and cached by ReportStore.
    function computeSummary(dateKey) {
      cmpState = null;
      var token = ++computeToken;
      subTitle.textContent = 'Chargement de ' + dateKey + '...';
      ReportStore.getSnapshot(dateKey).then(function(snap){
        if (token !== computeToken) return; // superseded by a newer request
        var agg = aggregate(snap);
        var rows = agg.keyOrder.map(function(k){ var e = agg.byKey[k]; return e.vals.concat(e.counts); });
        rows.sort(function(a, b){ return b[agg.sortIdx] - a[agg.sortIdx]; });
        var columns = agg.groupCols.concat(agg.countLabels);
        var kindTxt = (agg.wsKindSel === '__ALL__') ? 'tous' : agg.wsKindSel;
        subTitle.textContent = 'Date : ' + dateKey + '  -  sous-projets : ' + kindTxt + '  -  groupe par : ' + agg.groupCols.join(', ') +
          (agg.pivot ? ('  -  colonnes CategoryType : ' + agg.catVals.join(', ')) : '') +
          '  -  ' + agg.keyOrder.length + ' groupe(s)  -  ' + agg.kept + ' elements comptes';
        var defaultExclude = null;
        if (firstLoad) {
          firstLoad = false;
          var ctIdx2 = columns.indexOf('CategoryType');
          if (ctIdx2 !== -1) defaultExclude = {CategoryType: ['Model', 'Annotation']};
        }
        table.setData(columns, rows, {numGroupCols: agg.numGroupCols, defaultExcludeByColumn: defaultExclude});
      }, function(){
        if (token !== computeToken) return;
        subTitle.textContent = 'Erreur de lecture de l\'instantane ' + dateKey + '.';
      });
    }

    function compareSummary(dA, dB) {
      var kA = dA < dB ? dA : dB, kB = dA < dB ? dB : dA;
      cmpState = {a: kA, b: kB};
      var token = ++computeToken;
      subTitle.textContent = 'Chargement de ' + kA + ' et ' + kB + '...';
      Promise.all([ReportStore.getSnapshot(kA), ReportStore.getSnapshot(kB)]).then(function(pair){
        if (token !== computeToken) return; // superseded
        var aggA = aggregate(pair[0]), aggB = aggregate(pair[1]);
        var groupCols = aggB.groupCols, countLabels = aggB.countLabels;
        var keySet = {}, keyOrder = [];
        aggB.keyOrder.forEach(function(k){ if (!keySet[k]) { keySet[k] = true; keyOrder.push(k); } });
        aggA.keyOrder.forEach(function(k){ if (!keySet[k]) { keySet[k] = true; keyOrder.push(k); } });
        var cols = groupCols.slice();
        countLabels.forEach(function(l){ cols.push(l + ' \u0394'); });
        var rows = [];
        for (var i = 0; i < keyOrder.length; i++) {
          var k = keyOrder[i];
          var eA = aggA.byKey[k], eB = aggB.byKey[k];
          var vals = (eB ? eB.vals : eA.vals);
          var row = vals.slice();
          for (var c = 0; c < countLabels.length; c++) {
            var av = eA ? eA.counts[c] : 0, bv = eB ? eB.counts[c] : 0;
            row.push(bv - av);
          }
          rows.push(row);
        }
        var firstDeltaIdx = groupCols.length;
        rows = rows.filter(function(r){
          for (var c = firstDeltaIdx; c < r.length; c++) { if (Number(r[c]) !== 0) return true; }
          return false;
        });
        rows.sort(function(a, b){ return Math.abs(b[firstDeltaIdx]) - Math.abs(a[firstDeltaIdx]); });
        subTitle.textContent = 'Comparaison (difference B - A) : A=' + kA + '  ,  B=' + kB + '  -  groupe par : ' +
          groupCols.join(', ') + '  -  ' + rows.length + ' groupe(s) modifie(s). Vert = augmentation, Rouge = diminution.';
        table.setData(cols, rows, {
          numGroupCols: firstDeltaIdx,
          cellRenderFn: function(row, c, td){ return deltaCellRender(row, c, td, firstDeltaIdx); }
        });
      }, function(){
        if (token !== computeToken) return;
        subTitle.textContent = 'Erreur de lecture des instantanes ' + kA + ' / ' + kB + '.';
      });
    }

    wireTemplateUI('s_layout', makeTemplateStore('revitSummaryLayouts'),
      function(l){
        if (l.order) { groupOrder = l.order.filter(function(c){ return groupable.indexOf(c) !== -1; }); groupable.forEach(function(c){ if (groupOrder.indexOf(c) === -1) groupOrder.push(c); }); }
        if (l.checked) groupable.forEach(function(c){ groupChecked[c] = !!l.checked[c]; });
        if (l.counts) { cntElements.checked = !!l.counts.elements; cntTypes.checked = !!l.counts.types; cntFamilies.checked = !!l.counts.families; }
        if (l.wsKind) wsKindFilter.value = l.wsKind;
        if (typeof l.pivot !== 'undefined' && !pivotCatEl.disabled) pivotCatEl.checked = !!l.pivot;
        if (l.catChecked) {
          catTypeValues.forEach(function(v){ catChecked[v] = !!l.catChecked[v]; });
          var boxes = catValuesEl.querySelectorAll('input[type=checkbox]');
          for (var i = 0; i < boxes.length; i++) boxes[i].checked = !!catChecked[boxes[i].value];
        }
        renderGroupList();
        computeSummary(dateShow.value);
      },
      function(){
        return {
          order: groupOrder.slice(),
          checked: JSON.parse(JSON.stringify(groupChecked)),
          counts: {elements: cntElements.checked, types: cntTypes.checked, families: cntFamilies.checked},
          wsKind: wsKindFilter.value,
          pivot: pivotCatEl.checked,
          catChecked: JSON.parse(JSON.stringify(catChecked))
        };
      });

    document.getElementById('s_applyBtn').addEventListener('click', function(){
      if (dateShow.value !== ReportStore.latestKey() && !ReportStore.hasFolder()) {
        promptForFolder().then(function(){ fillDateSelects(); computeSummary(dateShow.value); }, function(){});
        return;
      }
      computeSummary(dateShow.value);
    });
    function doCmpSummary() {
      if (cmpDateA.value === cmpDateB.value) { alert('Choisissez deux dates differentes.'); return; }
      compareSummary(cmpDateA.value, cmpDateB.value);
    }
    document.getElementById('s_cmpBtn').addEventListener('click', function(){
      if (!ReportStore.hasFolder()) {
        promptForFolder().then(function(){ fillDateSelects(); doCmpSummary(); }, function(){});
        return;
      }
      doCmpSummary();
    });
    document.getElementById('s_cmpBackBtn').addEventListener('click', function(){ computeSummary(dateShow.value); });
    function rerun() { if (cmpState) compareSummary(cmpState.a, cmpState.b); else computeSummary(dateShow.value); }
    personField.addEventListener('change', rerun);
    personFilter.addEventListener('change', rerun);

    computeSummary(latestKey);
  }

  // =================================================================
  // DIAGRAMS TAB - a few "at a glance" charts computed from snapshot
  // data. The fixed charts and the ad-hoc "add a diagram" charts only
  // ever need the latest snapshot (already preloaded by bootApp(), so
  // rendering those is instant); only the Evolution trend genuinely
  // needs historical data, and reads exactly what it needs, lazily -
  // see renderTrend() below.
  // =================================================================
  function setupDiagramsTab(){
    var dateKeys = ReportStore.dateKeys();
    var latestKey = ReportStore.latestKey();
    var latest = ReportStore.getCached(latestKey); // preloaded by bootApp()
    var colIdx = {};
    latest.columns.forEach(function(c, i){ colIdx[c] = i; });
    var groupableCols = latest.columns.filter(function(c){ return c !== 'ElementId'; });
    var SERIES_PALETTE = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)',
      'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)'];
    var TREND_TOP_N = 6;
    var SLIDER_THRESHOLD = 8; // "too many dates" per the user's own ask

    function countBy(rows, ci, filterFn) {
      var counts = {};
      for (var r = 0; r < rows.length; r++) {
        if (filterFn && !filterFn(rows[r])) continue;
        var v = String(rows[r][ci]);
        counts[v] = (counts[v] || 0) + 1;
      }
      var arr = [];
      for (var k in counts) { if (Object.prototype.hasOwnProperty.call(counts, k)) arr.push({label: k, value: counts[k]}); }
      arr.sort(function(a, b){ return b.value - a.value; });
      return arr;
    }
    function topNWithOther(arr, n) {
      if (arr.length <= n) return arr;
      var top = arr.slice(0, n);
      var restSum = 0, restN = 0;
      for (var i = n; i < arr.length; i++) { restSum += arr[i].value; restN++; }
      top.push({label: 'Autres (' + restN + ')', value: restSum});
      return top;
    }
    function setChart(elId, data, drawFn, emptyMsg) {
      var el = document.getElementById(elId);
      if (!el) return;
      el.innerHTML = data.length ? drawFn(data) : '<p class="viz-empty">' + emptyMsg + '</p>';
    }

    // ---- 4 combinable filters - drive workset/creator/modifier charts,
    // every "added by parameter" chart, and the trend line. Rebuilding the
    // filter function from the live dropdown values on every read (instead
    // of caching one) keeps every consumer in sync with no extra wiring.
    var wsKindSelect = document.getElementById('viz_wsKind');
    var catTypeSelect = document.getElementById('viz_catType');
    var categorySelect = document.getElementById('viz_category');
    var personFieldSelect = document.getElementById('viz_personField');
    var personSelect = document.getElementById('viz_person');
    var wsKindIdx = colIdx['TypeSousProjet'];
    var catTypeIdx = colIdx['CategoryType'];
    var categoryIdx = colIdx['Category'];
    var creatorIdx0 = colIdx['Creator'];
    var lastChIdx0 = colIdx['LastChangedBy'];

    function fillSelect(select, values) {
      values.forEach(function(v){
        var o = document.createElement('option');
        o.value = v; o.textContent = v;
        select.appendChild(o);
      });
    }
    fillSelect(catTypeSelect, countBy(latest.rows, catTypeIdx, null).map(function(c){ return c.label; }));
    fillSelect(categorySelect, countBy(latest.rows, categoryIdx, null).map(function(c){ return c.label; }));
    // Scoped to the latest snapshot only, same trade-off as the
    // Recapitulatif tab's person filter - see its comment for why.
    (function fillPersonSelect(){
      var seen = {};
      var rows = latest.rows;
      for (var r = 0; r < rows.length; r++) {
        if (creatorIdx0 !== undefined) { var cv = String(rows[r][creatorIdx0]); if (cv) seen[cv] = true; }
        if (lastChIdx0 !== undefined) { var lv = String(rows[r][lastChIdx0]); if (lv) seen[lv] = true; }
      }
      Object.keys(seen).sort().forEach(function(n){
        var o = document.createElement('option'); o.value = n; o.textContent = n; personSelect.appendChild(o);
      });
    })();

    function personMatches(row) {
      var sel = personSelect.value;
      if (sel === '__ALL__') return true;
      var okC = creatorIdx0 !== undefined && String(row[creatorIdx0]) === sel;
      var okL = lastChIdx0 !== undefined && String(row[lastChIdx0]) === sel;
      var field = personFieldSelect.value;
      if (field === 'Creator') return okC;
      if (field === 'LastChangedBy') return okL;
      return okC || okL;
    }
    // The combined filter used by every chart EXCEPT the CategoryType donut,
    // which deliberately stays a whole-model view (see its own caption).
    function currentFilter() {
      var wsV = wsKindSelect.value, ctV = catTypeSelect.value, catV = categorySelect.value, perV = personSelect.value;
      var active = wsV !== '__ALL__' || ctV !== '__ALL__' || catV !== '__ALL__' || perV !== '__ALL__';
      if (!active) return null;
      return function(row){
        if (wsV !== '__ALL__' && wsKindIdx !== undefined && String(row[wsKindIdx]) !== wsV) return false;
        if (ctV !== '__ALL__' && catTypeIdx !== undefined && String(row[catTypeIdx]) !== ctV) return false;
        if (catV !== '__ALL__' && categoryIdx !== undefined && String(row[categoryIdx]) !== catV) return false;
        if (perV !== '__ALL__' && !personMatches(row)) return false;
        return true;
      };
    }
    function filterSummaryLabel() {
      var parts = [];
      if (wsKindSelect.value !== '__ALL__') parts.push('sous-projet ' + wsKindSelect.value);
      if (catTypeSelect.value !== '__ALL__') parts.push('type ' + catTypeSelect.value);
      if (categorySelect.value !== '__ALL__') parts.push('categorie ' + categorySelect.value);
      if (personSelect.value !== '__ALL__') parts.push(personSelect.value);
      return parts.length ? parts.join(' \u00b7 ') : 'tous les elements';
    }

    // ---- Save current filter values as the default applied automatically
    // the next time this report (or its sibling ElementReport/Summary file)
    // is opened - a single slot, not a named-template list, matching what
    // was asked for: "if I save, it becomes THE default." ----
    var DEFAULT_FILTERS_KEY = 'revitDiagramsDefaultFilters';
    function captureFilterValues() {
      return {
        wsKind: wsKindSelect.value, catType: catTypeSelect.value,
        category: categorySelect.value, personField: personFieldSelect.value, person: personSelect.value
      };
    }
    function applyFilterValues(v) {
      if (!v) return;
      [[wsKindSelect, v.wsKind], [catTypeSelect, v.catType], [categorySelect, v.category],
       [personFieldSelect, v.personField], [personSelect, v.person]].forEach(function(pair){
        var sel = pair[0], val = pair[1];
        if (val === undefined) return;
        // Only apply if that value actually exists as an option (the set of
        // Category/Personne values can differ between models) - otherwise
        // leave the dropdown on its default rather than silently no-op it.
        for (var i = 0; i < sel.options.length; i++) { if (sel.options[i].value === val) { sel.value = val; break; } }
      });
    }
    function updateDefaultStatus() {
      var d = Store.get(DEFAULT_FILTERS_KEY, null);
      document.getElementById('viz_defaultStatus').textContent = d ?
        'Filtres par defaut actifs - appliques automatiquement a l\'ouverture de ce rapport.' : '';
    }
    document.getElementById('viz_saveDefaultBtn').addEventListener('click', function(){
      Store.set(DEFAULT_FILTERS_KEY, captureFilterValues());
      updateDefaultStatus();
    });
    document.getElementById('viz_clearDefaultBtn').addEventListener('click', function(){
      Store.set(DEFAULT_FILTERS_KEY, null);
      updateDefaultStatus();
    });
    applyFilterValues(Store.get(DEFAULT_FILTERS_KEY, null));
    updateDefaultStatus();

    // ---- Fixed charts (workset / creator / modifier) ----
    function renderFixedCharts() {
      var f = currentFilter();
      var label = filterSummaryLabel();
      document.getElementById('viz_workset_sub').textContent = 'instantane du ' + latestKey + ' \u00b7 ' + label;
      document.getElementById('viz_creator_sub').textContent = label.charAt(0).toUpperCase() + label.slice(1);
      document.getElementById('viz_modifier_sub').textContent = label.charAt(0).toUpperCase() + label.slice(1);

      setChart('viz_workset', topNWithOther(countBy(latest.rows, colIdx['Workset'], f), 12),
        function(d){ return barChartSVG(d, {color: 'var(--series-1)'}); },
        'Aucun element pour ces filtres dans cet instantane.');
      setChart('viz_creator', topNWithOther(countBy(latest.rows, colIdx['Creator'], f), 12),
        function(d){ return barChartSVG(d, {color: 'var(--series-1)'}); },
        'Aucune donnee de createur disponible (modele non partage ?).');
      setChart('viz_modifier', topNWithOther(countBy(latest.rows, colIdx['LastChangedBy'], f), 12),
        function(d){ return barChartSVG(d, {color: 'var(--series-1)'}); },
        'Aucune donnee de modificateur disponible (modele non partage ?).');
    }

    // ---- CategoryType donut (whole model, not affected by the sous-projet
    // filter - it answers a different question) ----
    var CT_SLOTS = [
      {key: 'Model', color: 'var(--series-1)'},
      {key: 'Annotation', color: 'var(--series-2)'},
      {key: 'Internal', color: 'var(--series-3)'},
      {key: 'AnalyticalModel', color: 'var(--series-4)'}
    ];
    var ctCounts = countBy(latest.rows, colIdx['CategoryType'], null);
    var ctMap = {};
    ctCounts.forEach(function(c){ ctMap[c.label] = c.value; });
    var donutData = [];
    CT_SLOTS.forEach(function(slot){ if (ctMap[slot.key]) donutData.push({label: slot.key, value: ctMap[slot.key], color: slot.color}); });
    var ctKnown = {};
    CT_SLOTS.forEach(function(s){ ctKnown[s.key] = true; });
    var ctOtherSum = 0;
    ctCounts.forEach(function(c){ if (!ctKnown[c.label]) ctOtherSum += c.value; });
    if (ctOtherSum > 0) donutData.push({label: 'Autre', value: ctOtherSum, color: 'var(--series-5)'});
    setChart('viz_cattype', donutData, donutChartSVG, 'Aucune donnee de categorie disponible.');
    document.getElementById('viz_cattype_legend').innerHTML = donutData.map(function(d){
      return '<div><span class="viz-swatch" style="background:' + d.color + '"></span>' + escHtml(d.label) + ' - ' + d.value + '</div>';
    }).join('');

    // ---- Trend: element count per user over time, with a period slider
    // once there are "too many" dates to show meaningfully at once. ----
    var trendFromSlider = document.getElementById('viz_trendFrom');
    var trendToSlider = document.getElementById('viz_trendTo');
    var trendSliderRow = document.getElementById('viz_trendSliderRow');
    var trendRangeLabel = document.getElementById('viz_trendRangeLabel');
    var useSlider = false;
    function refreshTrendState() {
      dateKeys = ReportStore.dateKeys();
      useSlider = dateKeys.length > SLIDER_THRESHOLD;
      if (useSlider) {
        trendSliderRow.style.display = '';
        trendFromSlider.min = 0; trendFromSlider.max = dateKeys.length - 1; trendFromSlider.value = 0;
        trendToSlider.min = 0; trendToSlider.max = dateKeys.length - 1; trendToSlider.value = dateKeys.length - 1;
      } else {
        trendSliderRow.style.display = 'none';
      }
    }
    refreshTrendState();
    onAfterFolder(function(){ refreshTrendState(); renderTrend(); });

    // The one view that genuinely needs historical data (every date in
    // the selected range, not just the latest) - reads whichever of
    // those aren't already cached from ReportStore, showing progress
    // while it does, then caches them for next time (moving the slider
    // back over an already-loaded range afterward is instant).
    var trendRenderToken = 0;
    function renderTrend() {
      var fromIdx = 0, toIdx = dateKeys.length - 1;
      if (useSlider) {
        fromIdx = parseInt(trendFromSlider.value, 10);
        toIdx = parseInt(trendToSlider.value, 10);
        if (fromIdx > toIdx) { var t = fromIdx; fromIdx = toIdx; toIdx = t; }
        trendRangeLabel.textContent = dateKeys[fromIdx] + '  \u2192  ' + dateKeys[toIdx] +
          '  (' + (toIdx - fromIdx + 1) + ' / ' + dateKeys.length + ' instantanes)';
      }
      var xLabels = dateKeys.slice(fromIdx, toIdx + 1);
      var trendEl = document.getElementById('viz_trend');
      var legendEl = document.getElementById('viz_trend_legend');

      if (xLabels.length < 2) {
        if (!ReportStore.hasFolder()) {
          trendEl.innerHTML = '<p class="viz-empty">L\'evolution compare plusieurs exports. ' +
            '<button type="button" id="viz_trendLoadBtn" class="primary">Charger l\'historique</button></p>';
          legendEl.innerHTML = '';
          var b = document.getElementById('viz_trendLoadBtn');
          if (b) b.addEventListener('click', function(){
            b.disabled = true; b.textContent = 'Chargement...';
            promptForFolder().then(function(){}, function(){ b.disabled = false; b.textContent = 'Charger l\'historique'; });
          });
          return;
        }
        trendEl.innerHTML = '<p class="viz-empty">Pas assez d\'instantanes sur cette periode - relancez ce script ' +
          'a differents moments (avant/apres une seance de modelisation) pour voir la courbe de croissance ici.</p>';
        legendEl.innerHTML = '';
        return;
      }

      var creatorIdx = colIdx['Creator'];
      if (creatorIdx === undefined) {
        trendEl.innerHTML = '<p class="viz-empty">Pas de colonne Createur dans ces instantanes.</p>';
        legendEl.innerHTML = '';
        return;
      }

      var f = currentFilter();
      var token = ++trendRenderToken;
      trendEl.innerHTML = '<p class="viz-empty">Chargement des instantanes...</p>';
      legendEl.innerHTML = '';

      ReportStore.getMany(xLabels, function(done, total){
        if (token !== trendRenderToken || done === total) return; // final render replaces this anyway
        trendEl.innerHTML = '<p class="viz-empty">Chargement des instantanes... (' + done + ' / ' + total + ')</p>';
      }).then(function(snapsByLabel){
        if (token !== trendRenderToken) return; // a newer request superseded this one

        // Rank creators by their total over the SELECTED period (not the
        // whole history), so the top-N picked matches what the chart is
        // actually showing.
        var totals = {};
        xLabels.forEach(function(k){
          var rows = snapsByLabel[k].rows;
          for (var r = 0; r < rows.length; r++) {
            if (f && !f(rows[r])) continue;
            var name = String(rows[r][creatorIdx]);
            totals[name] = (totals[name] || 0) + 1;
          }
        });
        var names = Object.keys(totals).sort(function(a, b){ return totals[b] - totals[a]; });
        var topNames = names.slice(0, TREND_TOP_N);
        var topSet = {};
        topNames.forEach(function(n){ topSet[n] = true; });

        function countForDate(k, matchName) {
          var rows = snapsByLabel[k].rows;
          var cnt = 0;
          for (var r = 0; r < rows.length; r++) {
            if (f && !f(rows[r])) continue;
            var name = String(rows[r][creatorIdx]);
            if (matchName === null ? !topSet[name] : name === matchName) cnt++;
          }
          return cnt;
        }

        var series = topNames.map(function(name, i){
          return {label: name, color: SERIES_PALETTE[i % SERIES_PALETTE.length],
                  values: xLabels.map(function(k){ return countForDate(k, name); })};
        });
        if (names.length > TREND_TOP_N) {
          series.push({label: 'Autres (' + (names.length - TREND_TOP_N) + ')', color: 'var(--text-3)',
                       values: xLabels.map(function(k){ return countForDate(k, null); })});
        }

        trendEl.innerHTML = multiLineChartSVG(series, xLabels);
        legendEl.innerHTML = series.map(function(s){
          return '<div><span class="viz-swatch" style="background:' + s.color + '"></span>' + escHtml(s.label) + '</div>';
        }).join('');
      }, function(){
        if (token !== trendRenderToken) return;
        trendEl.innerHTML = '<p class="viz-empty">Erreur de lecture d\'un ou plusieurs instantanes.</p>';
      });
    }

    if (useSlider) {
      // Debounced: dragging fires many "input" events per second, and each
      // renderTrend() call may need to read not-yet-cached CSVs - only the
      // settled position needs to trigger that.
      var debouncedRenderTrend = debounce(renderTrend, 150);
      trendFromSlider.addEventListener('input', debouncedRenderTrend);
      trendToSlider.addEventListener('input', debouncedRenderTrend);
    }
    [wsKindSelect, catTypeSelect, categorySelect, personFieldSelect, personSelect].forEach(function(sel){
      sel.addEventListener('change', function(){
        renderFixedCharts();
        renderTrend();
        refreshExtraCharts();
      });
    });

    // ---- "Add a diagram by parameter(s)" - lets the user chart any column
    // (Category, Family, Type, ...) beyond the fixed three above, without
    // needing a code change. Up to 4 parameters in ONE combined diagram -
    // each bar is one unique combination of the chosen parameters' values
    // (e.g. "WS1 | Model | Alice"), not 4 separate charts - same idea as
    // the Recapitulatif tab's multi-column grouping, just as one ad-hoc
    // diagram here instead of a table. ----
    var addParamSelects = [
      document.getElementById('viz_addParamSelect0'), document.getElementById('viz_addParamSelect1'),
      document.getElementById('viz_addParamSelect2'), document.getElementById('viz_addParamSelect3')
    ];
    var addParamBtn = document.getElementById('viz_addParamBtn');
    var vizLayout = document.getElementById('viz_layout');
    var extraCharts = []; // [{id, cols}] - cols is the array of 1-4 column names for that card
    var extraSeq = 0;

    groupableCols.forEach(function(c){
      if (c === 'TypeSousProjet') return; // that's the filter itself, not a chart choice
      addParamSelects.forEach(function(sel){
        var o = document.createElement('option');
        o.value = c; o.textContent = c;
        sel.appendChild(o);
      });
    });

    function countByMulti(rows, colIndices, filterFn) {
      var counts = {};
      for (var r = 0; r < rows.length; r++) {
        if (filterFn && !filterFn(rows[r])) continue;
        var parts = colIndices.map(function(ci){ return String(rows[r][ci]); });
        var key = parts.join(' | ');
        counts[key] = (counts[key] || 0) + 1;
      }
      var arr = [];
      for (var k in counts) { if (Object.prototype.hasOwnProperty.call(counts, k)) arr.push({label: k, value: counts[k]}); }
      arr.sort(function(a, b){ return b.value - a.value; });
      return arr;
    }
    function renderExtraChart(id, cols) {
      var f = currentFilter();
      var colIndices = cols.map(function(c){ return colIdx[c]; });
      setChart(id, topNWithOther(countByMulti(latest.rows, colIndices, f), 12),
        function(d){ return barChartSVG(d, {color: 'var(--series-1)', labelWidth: cols.length > 1 ? 230 : 168}); },
        'Aucune donnee pour ce(s) parametre(s) avec ce filtre.');
    }
    function refreshExtraCharts() {
      extraCharts.forEach(function(e){ renderExtraChart(e.id, e.cols); });
    }
    function addChartForParams(cols) {
      extraSeq++;
      var id = 'viz_extra_' + extraSeq;
      var title = 'Elements par ' + cols.map(escHtml).join(' + ');
      var card = document.createElement('div');
      card.className = 'panel viz-card' + (cols.length > 1 ? ' viz-card-wide' : '');
      card.innerHTML =
        '<div class="viz-card-head"><h2>' + title + '</h2>' +
        '<button class="viz-remove" title="Retirer ce diagramme" type="button">&times;</button></div>' +
        '<p class="subtitle">Respecte les filtres ci-dessus' +
        (cols.length > 1 ? ' &middot; une barre par combinaison de valeurs (' + cols.map(escHtml).join(', ') + ')' : '') + '</p>' +
        '<div id="' + id + '"></div>';
      card.querySelector('.viz-remove').addEventListener('click', function(){
        vizLayout.removeChild(card);
        extraCharts = extraCharts.filter(function(e){ return e.id !== id; });
      });
      vizLayout.appendChild(card);
      extraCharts.push({id: id, cols: cols});
      renderExtraChart(id, cols);
    }

    addParamBtn.addEventListener('click', function(){
      // Up to 4 parameters, but ONE combined diagram - skip blanks (slots
      // 2-4 are optional) and skip a column picked twice in the same click.
      var chosen = [];
      addParamSelects.forEach(function(sel){
        var col = sel.value;
        if (col && chosen.indexOf(col) === -1) chosen.push(col);
      });
      if (chosen.length === 0) return;
      addChartForParams(chosen);
      // Reset the optional slots so the next click doesn't silently re-add
      // the same combination again; the first slot is left as-is since
      // picking the same primary parameter again is a normal thing to do.
      for (var i = 1; i < addParamSelects.length; i++) addParamSelects[i].value = '';
    });

    renderFixedCharts();
    renderTrend();
  }

  // =================================================================
  // Tab bar + quick global search wiring - wired once from bootApp(),
  // after all three tabs above have been set up.
  // =================================================================
  function setupTabBar() {
    var tabButtons = document.querySelectorAll('.tabbtn');
    var tabPanels = document.querySelectorAll('.tabpanel');
    function activateTab(name) {
      for (var i = 0; i < tabButtons.length; i++) {
        tabButtons[i].className = 'tabbtn' + (tabButtons[i].getAttribute('data-tab') === name ? ' active' : '');
      }
      for (var j = 0; j < tabPanels.length; j++) {
        tabPanels[j].className = 'tabpanel' + (tabPanels[j].getAttribute('data-tab') === name ? ' active' : '');
      }
      Store.set('revitReportTab', name);
      if (history.replaceState) history.replaceState(null, '', '#' + name);
    }
    for (var t = 0; t < tabButtons.length; t++) {
      (function(btn){ btn.addEventListener('click', function(){ activateTab(btn.getAttribute('data-tab')); }); })(tabButtons[t]);
    }
    var initialTab = (location.hash || '').replace('#', '') || Store.get('revitReportTab', __INITIAL_TAB__);
    if (initialTab !== 'detail' && initialTab !== 'summary' && initialTab !== 'diagrams') initialTab = __INITIAL_TAB__;
    activateTab(initialTab);
  }

  // =================================================================
  // Decompress the embedded payload: base64 -> gzip bytes -> JSON. Prefers
  // the native DecompressionStream (Chrome/Edge/Firefox/Safari have all
  // shipped it for years); falls back to the bundled pako if it's missing
  // or throws, so an unusual/older browser still works rather than being
  // stuck on the fallback folder-picker for no real reason.
  // =================================================================
  function b64ToBytes(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }
  function inflateWithPako(bytes) {
    if (typeof pako === 'undefined' || !pako.inflate) return Promise.reject(new Error('pako indisponible'));
    try { return Promise.resolve(pako.inflate(bytes, {to: 'string'})); }
    catch (e) { return Promise.reject(e); }
  }
  function decompressPayload(b64) {
    var bytes;
    try { bytes = b64ToBytes(b64); } catch (e) { return Promise.reject(new Error('donnees integrees invalides (base64)')); }
    var useNative = typeof DecompressionStream !== 'undefined' && typeof Response !== 'undefined';
    var textPromise;
    if (useNative) {
      try {
        var ds = new DecompressionStream('gzip');
        var writer = ds.writable.getWriter();
        writer.write(bytes);
        writer.close();
        textPromise = new Response(ds.readable).text().catch(function(){ return inflateWithPako(bytes); });
      } catch (e) {
        textPromise = inflateWithPako(bytes);
      }
    } else {
      textPromise = inflateWithPako(bytes);
    }
    return textPromise.then(function(text){ return JSON.parse(text); });
  }

  // =================================================================
  // Boot. Every run's data is embedded inline, compressed (see the
  // ReportStore comment at the top of this file) - decompress it once and
  // the app starts with everything already available, nothing to pick.
  //
  // The standalone #loadGate is only a fallback, for an HTML with no
  // embedded payload (a very old export) or one that fails to decompress -
  // then the folder must be picked before anything shows.
  // =================================================================
  var loadGate = document.getElementById('loadGate');
  var loadGateBtn = document.getElementById('loadGateBtn');
  var loadGateInput = document.getElementById('loadGateInput');
  var loadGateStatus = document.getElementById('loadGateStatus');

  function showGateStatus(msg, isError) {
    loadGateStatus.textContent = msg;
    loadGateStatus.className = 'hint' + (isError ? ' gate-error' : '');
  }

  function bootApp() {
    setupDetailTab();
    setupSummaryTab();
    setupDiagramsTab();
    setupTabBar();
    document.body.className = (document.body.className + ' bimreport-ready').trim();
  }

  function runFallbackGate(reason) {
    if (reason) showGateStatus(reason, true);
    loadGate.style.display = '';
    loadGateBtn.addEventListener('click', function(){ loadGateInput.click(); });
    loadGateInput.addEventListener('change', function onGateChange(){
      var files = loadGateInput.files;
      if (!files || files.length === 0) return;
      loadGateBtn.disabled = true;
      showGateStatus('Analyse du dossier...', false);
      ReportStore.loadFiles(files).then(function(info){
        showGateStatus(info.snapshotCount + ' instantane(s) trouve(s) - dernier : ' + info.latestKey + '. Chargement...', false);
        return ReportStore.getSnapshot(info.latestKey);
      }).then(function(){
        loadGateInput.removeEventListener('change', onGateChange);
        loadGate.style.display = 'none';
        bootApp();
      }, function(err){
        loadGateBtn.disabled = false;
        showGateStatus((err && err.message) ? err.message : 'Erreur de lecture du dossier choisi.', true);
      });
    });
  }

  var browserOk = !!(window.Promise && window.Uint8Array && window.atob);

  var PAYLOAD_GZ = null;
  try { PAYLOAD_GZ = __PAYLOAD_GZ__; } catch (e) { PAYLOAD_GZ = null; }

  if (!browserOk) {
    runFallbackGate('Ce navigateur est trop ancien pour ce rapport - utilisez une version recente de Chrome ou Edge.');
    loadGateBtn.disabled = true;
  } else if (PAYLOAD_GZ) {
    decompressPayload(PAYLOAD_GZ).then(function(payload){
      ReportStore.seedAll(payload);
      // Decode only the latest snapshot before showing anything - every
      // other embedded run stays undecoded until something actually asks
      // for it (see ReportStore.getSnapshot). Keeps this step's cost
      // independent of how many runs are embedded.
      return ReportStore.getSnapshot(ReportStore.latestKey());
    }).then(function(){
      bootApp();
    }, function(err){
      // Decompression failed (corrupted payload, truly ancient browser) -
      // fail closed to the folder picker rather than a blank page.
      runFallbackGate('Impossible de charger les donnees integrees (' +
        ((err && err.message) || 'erreur inconnue') + ') - choisissez le dossier du rapport.');
    });
  } else {
    runFallbackGate(null);
  }

})();
</script>"""


# Computed once (not inside build_app_html, which runs twice - once per
# output file) - see dict_encode_snapshot/gzip_b64 above for what this is.
# The CSV/JSON files above are already safely on disk regardless of what
# happens here - if packing/compressing the embedded payload hits something
# unanticipated, fail closed to an empty payload (the report falls back to
# its folder-picker screen) instead of letting the whole script error out
# and produce no report at all.
try:
    PAYLOAD_GZ = gzip_b64(json.dumps({
        "latest": latest_label,
        "snapshots": snapshots_encoded,
        "deltas": all_deltas,
        "availableParams": available_params,
    }, separators=(',', ':')))
except Exception:
    PAYLOAD_GZ = ""


def build_app_html(initial_tab):
    body = []
    body.append('<div id="filterFloat"></div>')

    body.append('<div id="appHeader"><div class="inner">')
    body.append('<div id="appTitle"><span class="projname">' + PROJECT_NAME_HTML + '</span> &middot; Rapport des elements')
    body.append('<br><span class="path">genere ' + run_label_display + ' &middot; outil par Manseur Mohamed</span></div>')
    body.append('<div class="tabbar">')
    body.append('<button class="tabbtn" data-tab="detail">Detail</button>')
    body.append('<button class="tabbtn" data-tab="summary">Recapitulatif</button>')
    body.append('<button class="tabbtn" data-tab="diagrams">Diagrammes</button>')
    body.append('</div>')
    body.append('<div class="appspacer"></div>')
    body.append('<button id="themeDarkBtn">Sombre</button><button id="themeLightBtn">Clair</button>')
    body.append('</div></div>')

    body.append('<div class="wrap">')

    # ---- Fallback gate - only shown if this HTML has no usable embedded
    # payload (a very old export) or one that fails to decompress. Normally
    # every run's data is embedded (gzip-compressed, see gzip_b64 above)
    # and the app starts immediately with full history, nothing to pick. ----
    body.append('<div id="loadGate" class="panel">')
    body.append('<h2>Charger le rapport</h2>')
    body.append('<p>Cette page n\'a pas d\'instantane integre (ancien export ?) - elle lit alors ses donnees directement ')
    body.append('dans le dossier d\'export (fichiers <code>Snapshot_*.csv</code>, <code>ParamsDelta_*.json</code>, ')
    body.append('<code>_params_baseline.json</code>).</p>')
    body.append('<p><strong>Choisissez le dossier de rapport</strong> (celui qui contient les fichiers <code>Snapshot_*.csv</code>) :</p>')
    body.append('<input type="file" id="loadGateInput" webkitdirectory directory multiple style="display:none"/>')
    body.append('<button id="loadGateBtn" class="primary">Choisir le dossier du rapport...</button>')
    body.append('<span class="hint" id="loadGateStatus"></span>')
    body.append('</div>')

    # ---- Detail tab ----
    body.append('<div class="tabpanel" data-tab="detail">')
    body.append('<div class="panel"><h2>Affichage</h2>')
    body.append('<div class="row"><strong>Date :</strong> <select id="d_dateShow"></select> <button id="d_showBtn">Afficher</button> ')
    body.append('<span class="hint">Par defaut : sous-projet Utilisateur seulement (comme Recapitulatif) - changez-le via le filtre de la colonne TypeSousProjet.</span></div>')
    body.append('<div class="row"><strong>Comparer deux dates :</strong> <select id="d_dateA"></select><select id="d_dateB"></select>')
    body.append('<button id="d_compareBtn" class="primary">Comparer</button><button id="d_backBtn">Derniere date</button>')
    body.append('<span class="legend" style="margin-left:12px;"><span style="background:#dcf6e4;"></span>Ajoute &nbsp;')
    body.append('<span style="background:#fbe0e1;"></span>Supprime &nbsp;<span style="background:#fdefcf;"></span>Modifie</span></div>')
    body.append('<div class="row" style="border-top:1px dashed var(--border);padding-top:8px;">')
    body.append('<strong>Gabarits de filtres :</strong> <input type="text" id="d_tmplName" placeholder="Nom du gabarit"/>')
    body.append('<button id="d_tmplSaveBtn">Enregistrer</button>')
    body.append('<select id="d_tmplSelect"></select><button id="d_tmplApplyBtn">Appliquer</button><button id="d_tmplDeleteBtn">Supprimer</button>')
    body.append('</div>')
    body.append('<div class="row" style="border-top:1px dashed var(--border);padding-top:8px;">')
    body.append('<strong>Ajouter des parametres :</strong> <select id="d_paramSelect"></select> ')
    body.append('<button id="d_paramAddBtn" class="primary">Ajouter</button> ')
    body.append('<span class="hint">Ajoute des colonnes en plus (les colonnes de base ne changent pas).</span>')
    body.append('</div>')
    body.append('<div class="row" id="d_paramChips"></div>')
    body.append('<div class="subtitle" id="d_subTitle"></div>')
    body.append('</div>')
    body.append('<div id="d_table"></div>')
    body.append('</div>')

    # ---- Summary tab ----
    body.append('<div class="tabpanel" data-tab="summary">')
    body.append('<div class="panel"><h2>Regroupement</h2>')
    body.append('<div class="row"><strong>Date :</strong> <select id="s_dateShow"></select>')
    body.append('<strong>Type de sous-projet :</strong> <select id="s_wsKindFilter">')
    body.append('<option value="__ALL__">Tous</option><option value="Utilisateur" selected>Utilisateur</option>')
    body.append('<option value="Familles">Familles</option><option value="Vues">Vues</option><option value="Projet/Autre">Projet/Autre</option>')
    body.append('</select>')
    body.append('<strong>Filtre par personne :</strong> <select id="s_personField"><option value="both">Createur ou Modificateur</option>')
    body.append('<option value="Creator">Createur</option><option value="LastChangedBy">Modificateur</option></select>')
    body.append('<select id="s_personFilter"><option value="__ALL__">Toutes</option></select></div>')
    body.append('<div class="row"><strong>Colonnes de regroupement :</strong></div>')
    body.append('<div class="row"><div id="s_groupColsList"></div></div>')
    body.append('<div class="row"><label><input type="checkbox" id="s_pivotCat" checked/> <strong>Separer CategoryType en colonnes</strong></label>')
    body.append('<span id="s_catValues"></span></div>')
    body.append('<div class="row"><strong>Colonnes de comptage :</strong> ')
    body.append('<label><input type="checkbox" id="s_cntElements" checked/> Nb elements</label>')
    body.append('<label><input type="checkbox" id="s_cntTypes"/> Nb Types distincts</label>')
    body.append('<label><input type="checkbox" id="s_cntFamilies"/> Nb Familles distinctes</label></div>')
    body.append('<div class="row hint">"Nb Types/Familles distincts" est correct seulement si Type et Family ne sont PAS des colonnes de regroupement.</div>')
    body.append('<div class="row"><button id="s_applyBtn" class="primary">Calculer</button>')
    body.append('<strong>Gabarit de disposition :</strong> <input type="text" id="s_layoutName" placeholder="Nom"/>')
    body.append('<button id="s_layoutSaveBtn">Enregistrer</button><select id="s_layoutSelect"></select>')
    body.append('<button id="s_layoutApplyBtn">Appliquer</button><button id="s_layoutDeleteBtn">Supprimer</button></div>')
    body.append('<div class="row" style="border-top:1px dashed var(--border);padding-top:8px;">')
    body.append('<strong>Comparer deux dates :</strong> <select id="s_cmpDateA"></select><select id="s_cmpDateB"></select>')
    body.append('<button id="s_cmpBtn" class="primary">Comparer</button><button id="s_cmpBackBtn">Vue simple</button>')
    body.append('<span class="hint">Compte a la date A, a la date B, et la difference, par groupe.</span></div>')
    body.append('<div class="subtitle" id="s_subTitle"></div>')
    body.append('</div>')
    body.append('<div id="s_table"></div>')
    body.append('</div>')

    # ---- Diagrams tab ----
    body.append('<div class="tabpanel" data-tab="diagrams">')

    body.append('<div class="panel"><h2>Filtres</h2>')
    body.append('<div class="row"><strong>Type de sous-projet :</strong> <select id="viz_wsKind">')
    body.append('<option value="__ALL__">Tous</option><option value="Utilisateur" selected>Utilisateur</option>')
    body.append('<option value="Familles">Familles</option><option value="Vues">Vues</option><option value="Projet/Autre">Projet/Autre</option>')
    body.append('</select></div>')
    body.append('<div class="row"><strong>Type de categorie :</strong> <select id="viz_catType"><option value="__ALL__">Tous</option></select></div>')
    body.append('<div class="row"><strong>Categorie :</strong> <select id="viz_category"><option value="__ALL__">Toutes</option></select></div>')
    body.append('<div class="row"><strong>Personne :</strong> <select id="viz_personField">')
    body.append('<option value="both">Createur ou modificateur</option><option value="Creator">Createur</option><option value="LastChangedBy">Modificateur</option>')
    body.append('</select> <select id="viz_person"><option value="__ALL__">Toutes</option></select></div>')
    body.append('<p class="hint">Ces 4 filtres s\'appliquent aux diagrammes par workset/createur/modificateur, aux diagrammes ajoutes ci-dessous, et a la courbe d\'evolution. Le diagramme de repartition par type de categorie reste une vue du modele complet.</p>')
    body.append('<div class="row" style="border-top:1px dashed var(--border);padding-top:8px;">')
    body.append('<button id="viz_saveDefaultBtn" class="primary">Enregistrer comme filtres par defaut</button>')
    body.append('<button id="viz_clearDefaultBtn">Effacer les filtres par defaut</button>')
    body.append('<span class="hint" id="viz_defaultStatus"></span></div>')
    body.append('<div class="row"><strong>Ajouter un diagramme par parametre(s) :</strong> ')
    body.append('<select id="viz_addParamSelect0"></select> <select id="viz_addParamSelect1"><option value="">-- (optionnel) --</option></select> ')
    body.append('<select id="viz_addParamSelect2"><option value="">-- (optionnel) --</option></select> <select id="viz_addParamSelect3"><option value="">-- (optionnel) --</option></select> ')
    body.append('<button id="viz_addParamBtn" class="primary">Ajouter</button> <span class="hint">Jusqu\'a 4 parametres a la fois (Category, Family, Type...), combines dans UN SEUL diagramme (une barre par combinaison de valeurs).</span></div>')
    body.append('</div>')

    body.append('<div class="viz-layout" id="viz_layout">')

    body.append('<div class="panel viz-card"><h2>Elements par workset</h2>')
    body.append('<p class="subtitle" id="viz_workset_sub"></p>')
    body.append('<div id="viz_workset"></div></div>')

    body.append('<div class="panel viz-card"><h2>Elements par createur</h2>')
    body.append('<p class="subtitle" id="viz_creator_sub"></p>')
    body.append('<div id="viz_creator"></div></div>')

    body.append('<div class="panel viz-card"><h2>Elements par dernier modificateur</h2>')
    body.append('<p class="subtitle" id="viz_modifier_sub"></p>')
    body.append('<div id="viz_modifier"></div></div>')

    body.append('<div class="panel viz-card"><h2>Repartition par type de categorie</h2>')
    body.append('<p class="subtitle">Tous les elements, non affecte par le filtre de sous-projet ci-dessus</p>')
    body.append('<div class="viz-donut-wrap"><div id="viz_cattype"></div><div id="viz_cattype_legend" class="viz-legend"></div></div></div>')

    body.append('</div>')  # .viz-layout

    body.append('<div class="panel viz-card viz-card-wide"><h2>Evolution du nombre d\'elements par utilisateur</h2>')
    body.append('<p class="subtitle">Un point par execution du script sur ce dossier &middot; respecte le filtre de sous-projet ci-dessus</p>')
    body.append('<div class="row" id="viz_trendSliderRow" style="display:none">')
    body.append('<strong>Periode :</strong> De <input type="range" id="viz_trendFrom"/> a <input type="range" id="viz_trendTo"/> ')
    body.append('<span class="hint" id="viz_trendRangeLabel"></span></div>')
    body.append('<div id="viz_trend"></div>')
    body.append('<div id="viz_trend_legend" class="viz-legend viz-legend-row"></div>')
    body.append('</div>')

    body.append('</div>')  # tabpanel diagrams

    body.append('</div>')  # .wrap

    # PAYLOAD_GZ is computed once at module level (not per call) - see
    # just above this function - so building both ElementReport.html and
    # ElementSummary.html doesn't re-scan every CSV and re-gzip twice.
    # Pure base64 text (A-Z a-z 0-9 + / =) never needs JS-string escaping,
    # so this is a plain substitution - no JSON.parse('...') wrapping
    # needed the way the uncompressed payload used to require.
    script = (APP_JS_TEMPLATE
              .replace("__PAYLOAD_GZ__", "'" + PAYLOAD_GZ + "'")
              .replace("__INITIAL_TAB__", "'" + initial_tab + "'"))

    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/><title>Rapport des elements</title>"
        + "<style>" + APP_CSS + "</style></head><body>"
        + "".join(body)
        + script
        + "</body></html>"
    )
    return html


html_content = build_app_html("detail")

with codecs.open(HTML_OUTPUT_PATH, "w", "utf-8") as f:
    f.write(html_content)

# ElementSummary.html is now the SAME app, just opened with the Summary
# tab active by default - it is fully standalone (not a redirect/stub),
# so any old shortcut or bookmark pointing at either filename keeps
# working exactly as before.
SUMMARY_OUTPUT_PATH = os.path.join(output_folder, "ElementSummary.html")
summary_content = build_app_html("summary")
with codecs.open(SUMMARY_OUTPUT_PATH, "w", "utf-8") as f:
    f.write(summary_content)

OUT = [
    "Snapshot written: " + THIS_SNAPSHOT_PATH,
    "Total snapshots found: " + str(total_snapshots),
    "Latest date shown by default: " + latest_label,
    "Parameter changes this run: " + str(len(params_delta)) + " element(s)" +
        (" (written to " + os.path.basename(delta_path) + ")" if params_delta else " (nothing changed - no delta file written)"),
    "Parameters known overall: " + str(len(available_params)),
    "HTML report (Detail tab): " + HTML_OUTPUT_PATH,
    "HTML report (Recapitulatif tab): " + SUMMARY_OUTPUT_PATH,
    "Ouvrez l'un des rapports HTML ci-dessus puis choisissez ce dossier " +
        "(" + output_folder + ") quand il le demande - les donnees sont lues directement depuis ces fichiers."
]
