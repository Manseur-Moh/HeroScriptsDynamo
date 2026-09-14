# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Supprimer les filtres de vue non utilises
# ============================================================

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('System')
clr.AddReference('System.Drawing')
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit.DB import *
from RevitServices.Persistence import DocumentManager

# Alias capture AVANT l'import de System.Windows.Forms : ce module definit
# aussi un symbole "View" (enum d'affichage de ListView) qui ecraserait
# sinon la classe Autodesk.Revit.DB.View du meme nom.
RevitView = View
RevitTransaction = Transaction

from System.Windows.Forms import *
from System.Drawing import *

doc = DocumentManager.Instance.CurrentDBDocument

def get_unused_filters(doc):
    """Filtres de vue (FilterElement) non appliques a aucune vue du projet.

    L'original utilisait un set() Python mais l'alimentait avec la methode
    .NET "Add" (majuscule) au lieu de la methode Python "add" (minuscule) :
    ceci levait une AttributeError immediate au premier appel, avant meme
    d'afficher une fenetre. On utilise ici directement les objets ElementId
    dans le set (ils s'y comparent correctement par valeur), ce qui evite
    aussi le recours a IntegerValue/Value (compatibilite entre versions de
    Revit).

    On collecte ParameterFilterElement et NON FilterElement : FilterElement
    est la classe de base commune aux filtres de vue (ParameterFilterElement)
    ET aux jeux de selection (SelectionFilterElement). Les jeux de selection
    n'apparaissent jamais dans View.GetFilters(), ils etaient donc tous
    listes comme "non utilises" et pouvaient etre supprimes par erreur."""
    all_filters = FilteredElementCollector(doc).OfClass(ParameterFilterElement).ToElements()
    all_views = FilteredElementCollector(doc).OfClass(RevitView).ToElements()

    used_filter_ids = set()
    for v in all_views:
        try:
            for fid in v.GetFilters():
                used_filter_ids.add(fid)
        except:
            continue

    unused = [f for f in all_filters if f.Id not in used_filter_ids]
    return sorted(unused, key=lambda f: f.Name)

class FilterForm(Form):
    def __init__(self):
        Form.__init__(self)
        self.unused_filters = get_unused_filters(doc)
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "06 - Supprimer les Filtres Non Utilises - 🎩 by Manseur Mohamed"
        self.Size = Size(500, 520)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        lbl_title = Label()
        lbl_title.Text = "Filtres Non Utilises"
        lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
        lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_title.Location = Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        lbl_sig = Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_sig.Location = Point(310, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        lbl_subtitle = Label()
        lbl_subtitle.Text = str(len(self.unused_filters)) + " filtre(s) trouve(s) - Cochez ceux a supprimer"
        lbl_subtitle.Font = Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = Point(20, 50)
        lbl_subtitle.AutoSize = True
        self.Controls.Add(lbl_subtitle)

        self.chk_list = CheckedListBox()
        self.chk_list.Font = Font("Segoe UI", 9)
        self.chk_list.Location = Point(20, 80)
        self.chk_list.Size = Size(440, 300)
        self.chk_list.CheckOnClick = True
        for f in self.unused_filters:
            self.chk_list.Items.Add(f.Name)
        self.Controls.Add(self.chk_list)

        self.btn_select_all = Button()
        self.btn_select_all.Text = "Tout (des)selectionner"
        self.btn_select_all.Font = Font("Segoe UI", 9)
        self.btn_select_all.Size = Size(210, 35)
        self.btn_select_all.Location = Point(20, 390)
        self.btn_select_all.BackColor = Color.FromArgb(100, 100, 100)
        self.btn_select_all.ForeColor = Color.White
        self.btn_select_all.FlatStyle = FlatStyle.Flat
        self.btn_select_all.FlatAppearance.BorderSize = 0
        self.btn_select_all.Click += self.OnSelectAll
        self.Controls.Add(self.btn_select_all)

        self.btn_delete = Button()
        self.btn_delete.Text = "Supprimer la selection"
        self.btn_delete.Font = Font("Segoe UI", 9, FontStyle.Bold)
        self.btn_delete.Size = Size(210, 35)
        self.btn_delete.Location = Point(250, 390)
        self.btn_delete.BackColor = Color.FromArgb(200, 40, 40)
        self.btn_delete.ForeColor = Color.White
        self.btn_delete.FlatStyle = FlatStyle.Flat
        self.btn_delete.FlatAppearance.BorderSize = 0
        self.btn_delete.Click += self.OnDelete
        self.Controls.Add(self.btn_delete)

        btn_close = Button()
        btn_close.Text = "Fermer"
        btn_close.Font = Font("Segoe UI", 9)
        btn_close.Size = Size(150, 30)
        btn_close.Location = Point(175, 440)
        btn_close.BackColor = Color.FromArgb(100, 100, 100)
        btn_close.ForeColor = Color.White
        btn_close.FlatStyle = FlatStyle.Flat
        btn_close.FlatAppearance.BorderSize = 0
        btn_close.Click += lambda s, e: self.Close()
        self.Controls.Add(btn_close)

    def OnSelectAll(self, sender, event):
        all_checked = all(self.chk_list.GetItemChecked(i) for i in range(self.chk_list.Items.Count))
        for i in range(self.chk_list.Items.Count):
            self.chk_list.SetItemChecked(i, not all_checked)

    def OnDelete(self, sender, event):
        checked_indices = list(self.chk_list.CheckedIndices)
        if not checked_indices:
            MessageBox.Show("Veuillez selectionner au moins un filtre a supprimer.", "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        checked_filters = [self.unused_filters[i] for i in checked_indices]

        result = MessageBox.Show(
            "Etes-vous sur de vouloir supprimer " + str(len(checked_filters)) + " filtre(s) ?\n\nCette action est irreversible.",
            "Confirmation de suppression",
            MessageBoxButtons.OKCancel,
            MessageBoxIcon.Question
        )
        if result != DialogResult.OK:
            return

        t = RevitTransaction(doc, "Supprimer filtres non utilises")
        try:
            t.Start()
            deleted_count = 0
            for filter_elem in checked_filters:
                try:
                    doc.Delete(filter_elem.Id)
                    deleted_count += 1
                except:
                    continue
            t.Commit()

            MessageBox.Show(str(deleted_count) + " filtre(s) supprime(s) avec succes.", "Succes", MessageBoxButtons.OK, MessageBoxIcon.Information)

            # Recharger la liste (les filtres supprimes ne doivent plus
            # apparaitre) plutot que de fermer la fenetre.
            self.unused_filters = get_unused_filters(doc)
            self.chk_list.Items.Clear()
            for f in self.unused_filters:
                self.chk_list.Items.Add(f.Name)
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            MessageBox.Show("Erreur lors de la suppression : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)

form = FilterForm()
form.ShowDialog()

OUT = "Termine - 🎩 by Manseur Mohamed"
