# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Isoler les elements par type de warning
# ISOLEMENT MANUEL UNIQUEMENT (via les boutons)
# Interface corrigee - bouton reset supprime
# ============================================================

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('RevitServices')
clr.AddReference('System')
clr.AddReference('System.Drawing')
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler
from RevitServices.Persistence import DocumentManager

RevitView = View
RevitTransaction = Transaction

from System.Windows.Forms import *
from System.Drawing import *
from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument
uidoc = DocumentManager.Instance.CurrentUIApplication.ActiveUIDocument

def id_value(element_id):
    if hasattr(element_id, "Value"):
        return element_id.Value
    return element_id.IntegerValue

# ============================================================
# 1. Collecte et regroupement des warnings par type de message
# ============================================================
def collect_warnings_by_type():
    data = {}
    seen_per_type = {}

    for w in doc.GetWarnings():
        msg = w.GetDescriptionText()

        if msg not in data:
            data[msg] = {'count': 0, 'element_ids': []}
            seen_per_type[msg] = set()

        data[msg]['count'] += 1

        for eid in w.GetFailingElements():
            vid = id_value(eid)
            if vid not in seen_per_type[msg]:
                seen_per_type[msg].add(vid)
                data[msg]['element_ids'].append(eid)

    sorted_types = sorted(data.keys(), key=lambda m: len(data[m]['element_ids']), reverse=True)
    return sorted_types, data

sorted_types, warnings_by_type = collect_warnings_by_type()

# ============================================================
# 2. Fonctions utilitaires
# ============================================================
def get_category_name(elem):
    try:
        if elem.Category is not None:
            return elem.Category.Name
    except:
        pass
    return "N/A"

def get_element_display_name(elem):
    try:
        if elem.Name:
            return elem.Name
    except:
        pass
    try:
        type_id = elem.GetTypeId()
        if type_id is not None and type_id != ElementId.InvalidElementId:
            etype = doc.GetElement(type_id)
            if etype is not None and etype.Name:
                return etype.Name
    except:
        pass
    return "Sans nom"

def find_views_for_elements(element_ids):
    views_found = []
    seen_ids = set()

    def add_view(v):
        vid = id_value(v.Id)
        if vid not in seen_ids:
            seen_ids.add(vid)
            views_found.append(v)

    try:
        model_elements = []
        for eid in element_ids:
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue

                owner_view_id = elem.OwnerViewId
                if owner_view_id is not None and owner_view_id != ElementId.InvalidElementId:
                    owner_view = doc.GetElement(owner_view_id)
                    if owner_view is not None:
                        add_view(owner_view)
                else:
                    model_elements.append(elem)
            except:
                continue

        if model_elements:
            all_views = FilteredElementCollector(doc).OfClass(RevitView).ToElements()

            for v in all_views:
                try:
                    if v.IsTemplate:
                        continue
                except:
                    continue

                matched = False
                for elem in model_elements:
                    try:
                        if elem.Category is not None and v.CategoryIsHidden(elem.Category):
                            continue
                        bb = elem.get_BoundingBox(v)
                        if bb is not None:
                            matched = True
                            break
                    except:
                        continue

                if matched:
                    add_view(v)
    except:
        pass

    return views_found

def isolate_in_view(view_id, element_ids):
    t = RevitTransaction(doc, "Isoler elements (warnings)")
    try:
        t.Start()
        view = doc.GetElement(view_id)
        try:
            view.ResetTemporaryHideIsolate()
        except:
            pass
        ids_list = List[ElementId](element_ids)
        view.IsolateElementsTemporary(ids_list)
        t.Commit()
        return True, None
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return False, str(ex)

# ============================================================
# 2 bis. Pont vers l'API Revit
# ============================================================
class WarningsExternalEventHandler(IExternalEventHandler):
    def __init__(self):
        # Sous le moteur CPython3 (pythonnet), une classe qui implemente une
        # interface .NET doit explicitement appeler le constructeur de
        # l'interface avant toute autre chose : sans cet appel, pythonnet
        # leve "TypeError: interface takes exactly one argument" a
        # l'instanciation. IronPython2 tolerait de sauter cet appel, pas
        # pythonnet.
        IExternalEventHandler.__init__(self)
        self.func = None

    def Execute(self, uiapp):
        if self.func is None:
            return
        try:
            self.func()
        except Exception as ex:
            MessageBox.Show("Erreur : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)

    def GetName(self):
        return "Isoler par Type de Warning - Handler"

_event_handler = WarningsExternalEventHandler()
_external_event = ExternalEvent.Create(_event_handler)

def run_in_api_context(func):
    _event_handler.func = func
    _external_event.Raise()

# ============================================================
# 3. Interface graphique (Windows Forms)
# ============================================================
class WarningManagerForm(Form):
    def __init__(self, warning_types, warnings_data):
        Form.__init__(self)
        self.warning_types = warning_types
        self.warnings_data = warnings_data
        self.selected_type = None
        self.views_list = []
        self.displayed_element_ids = []
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "02 - Isoler par Type de Warning - 🎩 by Manseur Mohamed"
        # Hauteur 690 (et non 660) : le pied de page est pose a y=615 et
        # mesure ~15px (bas a 630). Avec 660 de hauteur EXTERIEURE, la zone
        # client ne fait que ~624px (660 - barre de titre ~30 - bordures 2x3)
        # et le pied de page se retrouvait coupe. 690 => client ~654px.
        self.Size = Size(780, 690)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.TopMost = True
        self.BackColor = Color.FromArgb(240, 240, 240)
        self.Padding = Padding(15)

        # Titre
        lbl_title = Label()
        lbl_title.Text = "Gestionnaire de Warnings"
        lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
        lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_title.Location = Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        # Signature
        lbl_sig = Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_sig.Location = Point(570, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        # Note
        lbl_note = Label()
        lbl_note.Text = "Selectionnez un type de warning, puis cliquez sur un bouton pour isoler."
        lbl_note.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_note.ForeColor = Color.FromArgb(0, 120, 215)
        lbl_note.Location = Point(20, 48)
        lbl_note.AutoSize = True
        self.Controls.Add(lbl_note)

        # Label liste deroulante
        lbl_type = Label()
        lbl_type.Text = "Type d'avertissement :"
        lbl_type.Font = Font("Segoe UI", 10)
        lbl_type.Location = Point(20, 80)
        lbl_type.AutoSize = True
        self.Controls.Add(lbl_type)

        # Liste deroulante
        self.cmb_types = ComboBox()
        self.cmb_types.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_types.Font = Font("Segoe UI", 10)
        self.cmb_types.Location = Point(20, 105)
        self.cmb_types.Size = Size(730, 25)
        for t in self.warning_types:
            count = len(self.warnings_data[t]['element_ids'])
            self.cmb_types.Items.Add(t + "  [" + str(count) + " element(s)]")
        self.cmb_types.SelectedIndex = 0
        self.cmb_types.SelectedIndexChanged += self.OnTypeChanged
        self.Controls.Add(self.cmb_types)

        # Label elements
        lbl_elem = Label()
        lbl_elem.Text = "Elements concernes (double-clic pour selectionner et localiser) :"
        lbl_elem.Font = Font("Segoe UI", 10)
        lbl_elem.Location = Point(20, 145)
        lbl_elem.AutoSize = True
        self.Controls.Add(lbl_elem)

        # Liste des elements
        self.lst_elements = ListBox()
        self.lst_elements.Font = Font("Consolas", 9)
        self.lst_elements.Location = Point(20, 170)
        self.lst_elements.Size = Size(730, 140)
        self.lst_elements.DoubleClick += self.OnElementDoubleClick
        self.Controls.Add(self.lst_elements)

        # Label vues
        lbl_view = Label()
        lbl_view.Text = "Vues contenant ces elements (double-clic pour ouvrir) :"
        lbl_view.Font = Font("Segoe UI", 10)
        lbl_view.Location = Point(20, 325)
        lbl_view.AutoSize = True
        self.Controls.Add(lbl_view)

        # Liste des vues
        self.lst_views = ListBox()
        self.lst_views.Font = Font("Consolas", 9)
        self.lst_views.Location = Point(20, 350)
        self.lst_views.Size = Size(730, 140)
        self.lst_views.DoubleClick += self.OnOpenSelectedView
        self.Controls.Add(self.lst_views)

        # Separateur visuel avant les boutons
        separator = Label()
        separator.Text = "_________________________________________________________"
        separator.Font = Font("Segoe UI", 8)
        separator.ForeColor = Color.FromArgb(200, 200, 200)
        separator.Location = Point(20, 505)
        separator.AutoSize = True
        self.Controls.Add(separator)

        # Label actions
        lbl_actions = Label()
        lbl_actions.Text = "Actions :"
        lbl_actions.Font = Font("Segoe UI", 11, FontStyle.Bold)
        lbl_actions.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_actions.Location = Point(20, 520)
        lbl_actions.AutoSize = True
        self.Controls.Add(lbl_actions)

        # Bouton 1 : Isoler dans la vue courante
        self.btn_isolate_current = Button()
        self.btn_isolate_current.Text = "Isoler dans la vue courante"
        self.btn_isolate_current.Font = Font("Segoe UI", 11, FontStyle.Bold)
        self.btn_isolate_current.Size = Size(350, 50)
        self.btn_isolate_current.Location = Point(20, 555)
        self.btn_isolate_current.BackColor = Color.FromArgb(0, 120, 215)
        self.btn_isolate_current.ForeColor = Color.White
        self.btn_isolate_current.FlatStyle = FlatStyle.Flat
        self.btn_isolate_current.FlatAppearance.BorderSize = 0
        self.btn_isolate_current.Cursor = Cursors.Hand
        self.btn_isolate_current.Click += self.OnIsolateCurrent
        self.Controls.Add(self.btn_isolate_current)

        # Bouton 2 : Ouvrir la vue selectionnee et isoler
        self.btn_open_view = Button()
        self.btn_open_view.Text = "Ouvrir la vue selectionnee et isoler"
        self.btn_open_view.Font = Font("Segoe UI", 11, FontStyle.Bold)
        self.btn_open_view.Size = Size(350, 50)
        self.btn_open_view.Location = Point(390, 555)
        self.btn_open_view.BackColor = Color.FromArgb(16, 124, 16)
        self.btn_open_view.ForeColor = Color.White
        self.btn_open_view.FlatStyle = FlatStyle.Flat
        self.btn_open_view.FlatAppearance.BorderSize = 0
        self.btn_open_view.Cursor = Cursors.Hand
        self.btn_open_view.Click += self.OnOpenSelectedView
        self.Controls.Add(self.btn_open_view)

        # Info bas
        lbl_footer = Label()
        lbl_footer.Text = "L'isolement est temporaire - Fermez la fenetre pour annuler"
        lbl_footer.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_footer.ForeColor = Color.FromArgb(120, 120, 120)
        lbl_footer.Location = Point(20, 615)
        lbl_footer.AutoSize = True
        self.Controls.Add(lbl_footer)

        # Initialiser la liste SANS isolement automatique
        self.OnTypeChanged(None, None)

    def GetSelectedElementIds(self):
        if self.selected_type is None:
            return []
        return self.warnings_data[self.selected_type]['element_ids']

    def OnTypeChanged(self, sender, event):
        if self.cmb_types.SelectedIndex < 0:
            return

        self.selected_type = self.warning_types[self.cmb_types.SelectedIndex]
        element_ids = self.warnings_data[self.selected_type]['element_ids']

        run_in_api_context(self._update_for_type)

    def _update_for_type(self):
        element_ids = self.warnings_data[self.selected_type]['element_ids']

        # 1) Liste des elements
        self.displayed_element_ids = []
        self.lst_elements.Items.Clear()
        try:
            for eid in element_ids:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                cat = get_category_name(elem)
                name = get_element_display_name(elem)
                self.lst_elements.Items.Add("ID: " + str(id_value(eid)) + " | " + cat + " | " + name)
                self.displayed_element_ids.append(eid)
        except Exception as ex:
            MessageBox.Show("Erreur lors de l'affichage des elements : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)

        # 2) Liste des vues
        self.lst_views.Items.Clear()
        try:
            self.views_list = find_views_for_elements(element_ids)
        except Exception as ex:
            self.views_list = []
            MessageBox.Show("Erreur lors de la recherche des vues : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)

        if self.views_list:
            for v in self.views_list:
                view_name = v.Name if v.Name else "Sans nom"
                view_type = v.ViewType.ToString()
                self.lst_views.Items.Add("[" + view_type + "] " + view_name)
        else:
            self.lst_views.Items.Add("(Aucune vue trouvee - double-cliquez un element ci-dessus pour le localiser)")

    def OnIsolateCurrent(self, sender, event):
        element_ids = self.GetSelectedElementIds()
        if not element_ids:
            MessageBox.Show("Aucun element a isoler.")
            return

        def _do():
            current_view = uidoc.ActiveView
            ok, err = isolate_in_view(current_view.Id, element_ids)
            if ok:
                uidoc.RefreshActiveView()
                MessageBox.Show(str(len(element_ids)) + " element(s) isole(s) dans la vue courante.",
                                 "Succes", MessageBoxButtons.OK, MessageBoxIcon.Information)
            else:
                MessageBox.Show("Impossible d'isoler : " + str(err), "Erreur isolement", MessageBoxButtons.OK, MessageBoxIcon.Error)

        run_in_api_context(_do)

    def OnOpenSelectedView(self, sender, event):
        idx = self.lst_views.SelectedIndex
        if idx < 0 or idx >= len(self.views_list):
            MessageBox.Show("Selectionnez une vue dans la liste.")
            return

        element_ids = self.GetSelectedElementIds()
        v = self.views_list[idx]

        def _do():
            uidoc.ActiveView = v
            ok, err = (True, None)
            if element_ids:
                ok, err = isolate_in_view(v.Id, element_ids)
            uidoc.RefreshActiveView()
            if not ok:
                MessageBox.Show("Vue ouverte, mais impossible d'isoler : " + str(err), "Erreur isolement", MessageBoxButtons.OK, MessageBoxIcon.Error)

        run_in_api_context(_do)

    def OnElementDoubleClick(self, sender, event):
        idx = self.lst_elements.SelectedIndex
        if idx < 0 or idx >= len(self.displayed_element_ids):
            return

        eid = self.displayed_element_ids[idx]

        def _do():
            ids = List[ElementId]([eid])
            try:
                uidoc.Selection.SetElementIds(ids)
                uidoc.ShowElements(ids)
                uidoc.RefreshActiveView()
            except Exception as ex:
                MessageBox.Show("Impossible de localiser cet element : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)

        run_in_api_context(_do)

# ============================================================
# 4. Lancement de l'interface
# ============================================================
if not sorted_types:
    MessageBox.Show("Aucun warning trouve dans le document.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
    OUT = "Aucun warning"
else:
    try:
        form = WarningManagerForm(sorted_types, warnings_by_type)
        form.Show()
        OUT = "Termine - 🎩 by Manseur Mohamed"
    except Exception as ex:
        MessageBox.Show("Erreur: " + str(ex))
        OUT = "Erreur"