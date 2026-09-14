# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Supprimer les gabarits de vue non utilises
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

def get_unused_templates(doc):
    """Gabarits de vue (IsTemplate) non appliques a aucune vue du projet.

    Meme bug que le script des filtres non utilises : l'original alimentait
    un set() Python avec la methode .NET "Add" (majuscule) au lieu de
    "add" (minuscule), ce qui levait une AttributeError immediate avant
    meme d'afficher une fenetre. On utilise ici directement les objets
    ElementId dans le set (comparaison par valeur), ce qui evite aussi
    IntegerValue/Value (compatibilite entre versions de Revit)."""
    all_views = FilteredElementCollector(doc).OfClass(RevitView).ToElements()
    templates = [v for v in all_views if v.IsTemplate]

    used_template_ids = set()
    for v in all_views:
        try:
            if not v.IsTemplate and v.ViewTemplateId != ElementId.InvalidElementId:
                used_template_ids.add(v.ViewTemplateId)
        except:
            continue

    # Un gabarit peut aussi etre defini comme gabarit PAR DEFAUT d'un type de
    # vue (ViewFamilyType.DefaultTemplateId) sans etre applique a aucune vue
    # existante : il est bel et bien utilise et le supprimer changerait le
    # comportement du projet a la creation des vues suivantes.
    try:
        for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements():
            try:
                default_id = vft.DefaultTemplateId
                if default_id is not None and default_id != ElementId.InvalidElementId:
                    used_template_ids.add(default_id)
            except:
                continue
    except:
        pass

    unused = [t for t in templates if t.Id not in used_template_ids]
    return sorted(unused, key=lambda t: t.Name)

class ViewTemplateForm(Form):
    def __init__(self):
        Form.__init__(self)
        self.unused_templates = get_unused_templates(doc)
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "07 - Supprimer les Gabarits de Vue Non Utilises - 🎩 by Manseur Mohamed"
        self.Size = Size(500, 520)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        lbl_title = Label()
        lbl_title.Text = "Gabarits Non Utilises"
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
        lbl_subtitle.Text = str(len(self.unused_templates)) + " gabarit(s) trouve(s) - Cochez ceux a supprimer"
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
        for t in self.unused_templates:
            self.chk_list.Items.Add(t.Name)
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
            MessageBox.Show("Veuillez selectionner au moins un gabarit a supprimer.", "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        checked_templates = [self.unused_templates[i] for i in checked_indices]

        result = MessageBox.Show(
            "Etes-vous sur de vouloir supprimer " + str(len(checked_templates)) + " gabarit(s) ?\n\nCette action est irreversible.",
            "Confirmation de suppression",
            MessageBoxButtons.OKCancel,
            MessageBoxIcon.Question
        )
        if result != DialogResult.OK:
            return

        t = RevitTransaction(doc, "Supprimer gabarits de vue non utilises")
        try:
            t.Start()
            deleted_count = 0
            for template in checked_templates:
                try:
                    doc.Delete(template.Id)
                    deleted_count += 1
                except:
                    continue
            t.Commit()

            MessageBox.Show(str(deleted_count) + " gabarit(s) supprime(s) avec succes.", "Succes", MessageBoxButtons.OK, MessageBoxIcon.Information)

            # Recharger la liste (les gabarits supprimes ne doivent plus
            # apparaitre) plutot que de fermer la fenetre.
            self.unused_templates = get_unused_templates(doc)
            self.chk_list.Items.Clear()
            for template in self.unused_templates:
                self.chk_list.Items.Add(template.Name)
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            MessageBox.Show("Erreur lors de la suppression : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)

form = ViewTemplateForm()
form.ShowDialog()

OUT = "Termine - 🎩 by Manseur Mohamed"
