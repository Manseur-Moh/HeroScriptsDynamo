# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Gestion des Warnings par Createur
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

# Alias capture AVANT l'import de System.Windows.Forms : ce module definit
# aussi un symbole "View" (enum d'affichage de ListView) et pourrait
# ambiguiser d'autres noms Revit. On fige la reference dont on a besoin
# avant que le wildcard import suivant ne l'ecrase.
RevitTransaction = Transaction

from System.Windows.Forms import *
from System.Drawing import *
from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument
uidoc = DocumentManager.Instance.CurrentUIApplication.ActiveUIDocument

# 1. Collecte des warnings avec createur
def get_creator(element_id):
    # Le "createur" d'un element n'est disponible que via le worksharing
    # (WorksharingTooltipInfo), il n'existe pas de BuiltInParameter generique
    # pour ca. Si le modele n'est pas en worksharing, on retombe sur "Inconnu".
    try:
        info = WorksharingUtils.GetWorksharingTooltipInfo(doc, element_id)
        if info and info.Creator:
            return info.Creator
    except:
        pass
    return "Inconnu"

def collect_warnings_data():
    # Regroupe les warnings actuels du document par createur.
    data = {}
    warnings = doc.GetWarnings()

    for w in warnings:
        msg = w.GetDescriptionText()
        failing_ids = w.GetFailingElements()  # IList[ElementId], pas des Element

        for eid in failing_ids:
            elem = doc.GetElement(eid)
            if elem is None:
                continue

            creator = get_creator(eid)

            if creator not in data:
                data[creator] = {}

            if msg not in data[creator]:
                data[creator][msg] = []

            data[creator][msg].append({
                'element': elem,
                'element_id': eid
            })

    return sorted(data.keys()), data

creators, data_by_creator = collect_warnings_data()

# 2. Fonctions d'action sur le document (isolement / reinitialisation)
def isolate_elements(view_id, element_ids):
    # Doit etre appelee depuis un contexte API Revit valide (a l'interieur
    # d'un ExternalEvent.Execute), sinon Transaction.Start() leve "Starting
    # a transaction from an external application running outside of API
    # context is not allowed."
    t = RevitTransaction(doc, "Isoler elements (warnings)")
    try:
        t.Start()
        view = doc.GetElement(view_id)
        # Reinitialiser un eventuel isolement temporaire deja actif avant
        # d'appliquer le nouveau : Revit refuse un second
        # IsolateElementsTemporary tant que le precedent n'a pas ete leve.
        # ResetTemporaryHideIsolate() leve une exception si la vue n'a AUCUN
        # isolement actif (ce n'est pas un no-op) : on l'encadre donc dans
        # son propre try/except plutot que de se fier a
        # IsTemporaryHideIsolateActive(), qui s'est revele peu fiable.
        try:
            view.ResetTemporaryHideIsolate()
        except:
            pass
        ids_list = List[ElementId](element_ids)
        view.IsolateElementsTemporary(ids_list)
        t.Commit()
        return True
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        MessageBox.Show("Impossible d'isoler : " + str(ex), "Erreur isolement", MessageBoxButtons.OK, MessageBoxIcon.Error)
        return False

# 2 bis. Pont vers l'API Revit depuis la fenetre non-modale
#
# Une fois le script Dynamo termine (OUT assigne), on n'est plus dans le
# contexte d'execution du graphe : Revit refuse alors tout appel a son API
# venant directement d'un evenement WinForms (clic de bouton...). Le
# mecanisme officiel pour continuer a utiliser l'API depuis une fenetre
# modeless est ExternalEvent : on lui confie une fonction Python a executer,
# et Revit la rappelle des qu'il est pret, dans un contexte valide.
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
        return "Gestion Warnings par Createur - Handler"

_event_handler = WarningsExternalEventHandler()
_external_event = ExternalEvent.Create(_event_handler)

def run_in_api_context(func):
    _event_handler.func = func
    _external_event.Raise()

# 3. Interface
class WarningForm(Form):
    def __init__(self, creators, data):
        Form.__init__(self)
        self.creators = creators
        self.data = data
        self.selected_creator = None
        self.selected_warning = None
        self.element_ids = []
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "01 - Gestion des Warnings par Createur - 🎩 by Manseur Mohamed"
        self.Size = Size(800, 350)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.TopMost = True  # reste visible pendant qu'on clique dans Revit
        self.BackColor = Color.FromArgb(240, 240, 240)

        # Titre
        lbl_title = Label()
        lbl_title.Text = "Gestionnaire de Warnings par Createur"
        lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
        lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_title.Location = Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        lbl_sig = Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_sig.Location = Point(600, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        # Createur
        lbl_creator = Label()
        lbl_creator.Text = "1. Selectionnez le createur :"
        lbl_creator.Font = Font("Segoe UI", 11, FontStyle.Bold)
        lbl_creator.Location = Point(20, 60)
        lbl_creator.AutoSize = True
        self.Controls.Add(lbl_creator)

        self.cmb_creators = ComboBox()
        self.cmb_creators.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_creators.Font = Font("Segoe UI", 11)
        self.cmb_creators.Location = Point(20, 90)
        self.cmb_creators.Size = Size(750, 28)
        for c in self.creators:
            count = sum(len(v) for v in self.data[c].values())
            self.cmb_creators.Items.Add(c + " [" + str(count) + " warning(s)]")
        self.cmb_creators.SelectedIndexChanged += self.OnCreatorChanged
        self.Controls.Add(self.cmb_creators)

        # Warning type
        lbl_warning = Label()
        lbl_warning.Text = "2. Selectionnez le warning :"
        lbl_warning.Font = Font("Segoe UI", 11, FontStyle.Bold)
        lbl_warning.Location = Point(20, 135)
        lbl_warning.AutoSize = True
        self.Controls.Add(lbl_warning)

        self.cmb_warnings = ComboBox()
        self.cmb_warnings.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_warnings.Font = Font("Segoe UI", 10)
        self.cmb_warnings.Location = Point(20, 165)
        self.cmb_warnings.Size = Size(750, 25)
        self.cmb_warnings.SelectedIndexChanged += self.OnWarningChanged
        self.Controls.Add(self.cmb_warnings)

        # Info
        self.lbl_info = Label()
        self.lbl_info.Text = "0 element(s)"
        self.lbl_info.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.lbl_info.ForeColor = Color.FromArgb(0, 100, 0)
        self.lbl_info.Location = Point(20, 205)
        self.lbl_info.AutoSize = True
        self.Controls.Add(self.lbl_info)

        # Boutons
        self.btn_current = Button()
        self.btn_current.Text = "Isoler dans la VUE COURANTE"
        self.btn_current.Font = Font("Segoe UI", 11, FontStyle.Bold)
        self.btn_current.Size = Size(750, 60)
        self.btn_current.Location = Point(20, 240)
        self.btn_current.BackColor = Color.FromArgb(0, 120, 215)
        self.btn_current.ForeColor = Color.White
        self.btn_current.FlatStyle = FlatStyle.Flat
        self.btn_current.FlatAppearance.BorderSize = 0
        self.btn_current.Click += self.OnIsolateCurrent
        self.Controls.Add(self.btn_current)

        if self.cmb_creators.Items.Count > 0:
            self.cmb_creators.SelectedIndex = 0

    def OnCreatorChanged(self, sender, e):
        self.cmb_warnings.Items.Clear()
        self.element_ids = []

        if self.cmb_creators.SelectedIndex < 0:
            return

        self.selected_creator = self.creators[self.cmb_creators.SelectedIndex]
        creator_data = self.data[self.selected_creator]

        warning_types = sorted(creator_data.keys(), key=lambda x: len(creator_data[x]), reverse=True)

        for wt in warning_types:
            count = len(creator_data[wt])
            self.cmb_warnings.Items.Add(wt + " [" + str(count) + " element(s)]")

        if self.cmb_warnings.Items.Count > 0:
            self.cmb_warnings.SelectedIndex = 0

    def OnWarningChanged(self, sender, e):
        self.element_ids = []

        if self.cmb_warnings.SelectedIndex < 0:
            return

        creator_data = self.data[self.selected_creator]
        warning_types = sorted(creator_data.keys(), key=lambda x: len(creator_data[x]), reverse=True)
        self.selected_warning = warning_types[self.cmb_warnings.SelectedIndex]

        elements_data = creator_data[self.selected_warning]
        self.element_ids = [d['element_id'] for d in elements_data]

        self.lbl_info.Text = str(len(self.element_ids)) + " element(s) concerne(s)"

    def OnIsolateCurrent(self, sender, e):
        if not self.element_ids:
            MessageBox.Show("Aucun element a isoler.")
            return

        element_ids = self.element_ids

        def _do():
            current_view = uidoc.ActiveView
            if isolate_elements(current_view.Id, element_ids):
                uidoc.RefreshActiveView()
                MessageBox.Show(str(len(element_ids)) + " element(s) isole(s) dans la vue courante.", "Succes", MessageBoxButtons.OK, MessageBoxIcon.Information)

        run_in_api_context(_do)

# Lancement
if not creators:
    MessageBox.Show("Aucun warning trouve.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
    OUT = "Aucun warning"
else:
    try:
        form = WarningForm(creators, data_by_creator)
        # Show() (non-modal) au lieu de ShowDialog() : la fenetre reste
        # ouverte sans bloquer Revit, pour pouvoir corriger les warnings
        # directement dans le modele pendant qu'elle est affichee. Toute
        # action Revit declenchee depuis la fenetre passe par ExternalEvent
        # (voir run_in_api_context) puisque le graphe Dynamo est deja
        # termine a ce moment-la.
        form.Show()
        OUT = "Termine - 🎩 by Manseur Mohamed"
    except Exception as ex:
        MessageBox.Show("Erreur: " + str(ex))
        OUT = "Erreur"
