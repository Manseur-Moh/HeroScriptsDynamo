# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Creation de Vues par Sous-Projet (Workset)
#
# Cree, pour chaque sous-projet coche dans l'interface, une vue 3D ET une
# vue en plan, toutes deux cadrees sur les elements de ce sous-projet, avec
# dans ces vues UNIQUEMENT ce sous-projet visible - tous les autres
# sous-projets sont fermes (masques) via la visibilite par sous-projet de
# la vue.
#
# La vue en plan a un View Range etendu sur toute la hauteur du batiment
# (du niveau le plus bas au plus haut, avec une large marge) pour montrer
# les elements de TOUS les niveaux, pas seulement ceux proches du niveau
# associe a la vue - et un style d'affichage Filaire pour voir a travers
# les elements (murs, dalles...) plutot que d'avoir les faces opaques d'un
# niveau masquer ce qu'il y a en dessous/au-dessus.
# ============================================================

import clr
import math

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

ZOOM_FACTOR = 1.3    # marge autour de la boite englobante du sous-projet
VIEW_PREFIX = "SP_"  # prefixe des vues creees (Sous-Projet)
VIEW_RANGE_MARGIN = 50.0  # pieds - marge au-dessus du niveau le plus haut / en dessous du plus bas


# ============================================================
# COLLECTE DES SOUS-PROJETS (WORKSETS) ET NIVEAUX
# ============================================================
def get_worksets_utilisateur(doc):
    # WorksetKind.UserWorkset (pas "UserWorkable", qui n'existe pas dans
    # l'API Revit et levait AttributeError des le chargement du script).
    collector = FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)
    return sorted([ws for ws in collector], key=lambda w: w.Name)


def get_niveaux_tries(doc):
    niveaux = FilteredElementCollector(doc).OfClass(Level).ToElements()
    return sorted(niveaux, key=lambda l: l.Elevation)


# ============================================================
# CADRAGE : BOITE ENGLOBANTE DES ELEMENTS D'UN SOUS-PROJET
# ============================================================
def is_finite(value):
    """Verifie qu'une valeur est finie (pas NaN, pas infini)."""
    try:
        return not (math.isnan(value) or math.isinf(value))
    except Exception:
        return False


def get_bbox_for_workset(doc, workset):
    # IMPORTANT : ne JAMAIS construire un XYZ avec float('inf') comme
    # sentinelle de depart - le constructeur XYZ de l'API Revit valide que
    # chaque coordonnee est finie et leve immediatement "x must be a finite
    # number" des la premiere ligne, avant meme de parcourir les elements.
    # On suit donc le "premier element trouve initialise min/max" plutot
    # qu'une initialisation a l'infini.
    min_pt = None
    max_pt = None
    found = False

    filter_ws = ElementWorksetFilter(workset.Id)
    collector = FilteredElementCollector(doc).WherePasses(filter_ws).WhereElementIsNotElementType()
    for elem in collector:
        try:
            bb = elem.get_BoundingBox(None)
            if bb is None:
                continue
            if not (is_finite(bb.Min.X) and is_finite(bb.Min.Y) and is_finite(bb.Min.Z) and
                    is_finite(bb.Max.X) and is_finite(bb.Max.Y) and is_finite(bb.Max.Z)):
                continue

            if not found:
                min_pt = XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z)
                max_pt = XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z)
                found = True
            else:
                min_pt = XYZ(min(min_pt.X, bb.Min.X), min(min_pt.Y, bb.Min.Y), min(min_pt.Z, bb.Min.Z))
                max_pt = XYZ(max(max_pt.X, bb.Max.X), max(max_pt.Y, bb.Max.Y), max(max_pt.Z, bb.Max.Z))
        except Exception:
            pass

    if not found:
        return None, None
    return min_pt, max_pt


def make_section_box(min_pt, max_pt, zoom_factor):
    center = XYZ((min_pt.X + max_pt.X) / 2.0, (min_pt.Y + max_pt.Y) / 2.0, (min_pt.Z + max_pt.Z) / 2.0)
    size_x = max((max_pt.X - min_pt.X) * zoom_factor, 5.0)
    size_y = max((max_pt.Y - min_pt.Y) * zoom_factor, 5.0)
    size_z = max((max_pt.Z - min_pt.Z) * zoom_factor, 5.0)

    box = BoundingBoxXYZ()
    box.Min = XYZ(center.X - size_x / 2.0, center.Y - size_y / 2.0, center.Z - size_z / 2.0)
    box.Max = XYZ(center.X + size_x / 2.0, center.Y + size_y / 2.0, center.Z + size_z / 2.0)
    return box


# ============================================================
# VIEW RANGE ETENDU (pour que le plan montre TOUS les niveaux)
# ============================================================
def set_full_view_range(view_plan, levels_sorted, margin):
    """Etend le View Range du plan sur toute la hauteur du batiment (du
    niveau le plus bas au plus haut, avec une large marge) au lieu de la
    plage etroite par defaut autour du niveau associe a la vue - sinon
    seuls les elements proches de ce niveau apparaitraient, meme avec le
    sous-projet rendu visible."""
    if not levels_sorted:
        return
    try:
        view_range = view_plan.GetViewRange()
        lowest = levels_sorted[0]
        highest = levels_sorted[-1]

        view_range.SetLevelId(PlanViewPlane.TopClipPlane, highest.Id)
        view_range.SetOffset(PlanViewPlane.TopClipPlane, margin)

        view_range.SetLevelId(PlanViewPlane.CutPlane, highest.Id)
        view_range.SetOffset(PlanViewPlane.CutPlane, margin / 2.0)

        view_range.SetLevelId(PlanViewPlane.BottomClipPlane, lowest.Id)
        view_range.SetOffset(PlanViewPlane.BottomClipPlane, -margin)

        view_range.SetLevelId(PlanViewPlane.ViewDepthPlane, lowest.Id)
        view_range.SetOffset(PlanViewPlane.ViewDepthPlane, -margin)

        view_plan.SetViewRange(view_range)
    except Exception:
        pass


# ============================================================
# CREATION DES VUES (3D + PLAN) POUR UN SOUS-PROJET
# ============================================================
def find_existing_view(doc, view_name):
    """Retrouve une vue existante par son nom exact (hors gabarits).

    Relancer le script sur une vue deja creee ne la recree plus depuis
    zero (ce qui perdait tout ajustement fait a la main dessus depuis -
    annotations ajoutees, placement sur feuille...) : si le nom existe
    deja, on se contente de remettre a jour la visibilite par sous-projet
    (fermer tous les autres, ne laisser que celui de la vue), et on ne
    touche a rien d'autre."""
    for v in FilteredElementCollector(doc).OfClass(RevitView).ToElements():
        try:
            if not v.IsTemplate and v.Name == view_name:
                return v
        except Exception:
            continue
    return None


def apply_workset_visibility(view, workset, all_worksets):
    for ws in all_worksets:
        visibilite = WorksetVisibility.Visible if ws.Id == workset.Id else WorksetVisibility.Hidden
        try:
            view.SetWorksetVisibility(ws.Id, visibilite)
        except Exception:
            pass


def create_workset_3d_view(doc, workset, all_worksets, view_name, zoom_factor):
    vft_3d = None
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if vft.ViewFamily == ViewFamily.ThreeDimensional:
            vft_3d = vft
            break
    if vft_3d is None:
        return None, "Aucun type de vue 3D disponible dans ce document."

    new_view = View3D.CreateIsometric(doc, vft_3d.Id)
    new_view.Name = view_name

    apply_workset_visibility(new_view, workset, all_worksets)

    min_pt, max_pt = get_bbox_for_workset(doc, workset)
    if min_pt is not None:
        try:
            new_view.SetSectionBox(make_section_box(min_pt, max_pt, zoom_factor))
            new_view.IsSectionBoxActive = True
        except Exception:
            pass

    return new_view, None


def create_workset_plan_view(doc, workset, all_worksets, view_name, base_level, levels_sorted, zoom_factor):
    vft_plan = None
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if vft.ViewFamily == ViewFamily.FloorPlan:
            vft_plan = vft
            break
    if vft_plan is None:
        return None, "Aucun type de plan d'etage disponible dans ce document."

    new_view = ViewPlan.Create(doc, vft_plan.Id, base_level.Id)
    new_view.Name = view_name

    apply_workset_visibility(new_view, workset, all_worksets)

    # Filaire : permet de voir a travers les elements (murs, dalles,
    # plafonds...) plutot que d'avoir les faces opaques d'un niveau masquer
    # ce qu'il y a en dessous/au-dessus - utile puisque le View Range
    # etendu ci-dessous fait apparaitre tous les niveaux a la fois.
    try:
        new_view.DisplayStyle = DisplayStyle.Wireframe
    except Exception:
        pass

    set_full_view_range(new_view, levels_sorted, VIEW_RANGE_MARGIN)

    min_pt, max_pt = get_bbox_for_workset(doc, workset)
    if min_pt is not None:
        try:
            new_view.CropBox = make_section_box(min_pt, max_pt, zoom_factor)
            new_view.CropBoxActive = True
            new_view.CropBoxVisible = True
        except Exception:
            pass

    return new_view, None


# ============================================================
# INTERFACE (Windows Forms - meme charte que les autres scripts du projet)
# ============================================================
class WorksetViewsForm(Form):
    def __init__(self, worksets):
        Form.__init__(self)
        self.worksets = worksets
        self.selected_worksets = []
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "09 - Creation de Vues par Sous-Projet - 🎩 by Manseur Mohamed"
        self.Size = Size(500, 590)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        lbl_title = Label()
        lbl_title.Text = "Creation de Vues par Sous-Projet"
        lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
        lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_title.Location = Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        lbl_sig = Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_sig.Location = Point(300, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        lbl_subtitle = Label()
        lbl_subtitle.Text = str(len(self.worksets)) + " sous-projet(s) trouve(s) - Cochez ceux a traiter, puis le(s) type(s) de vue ci-dessous"
        lbl_subtitle.Font = Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = Point(20, 50)
        lbl_subtitle.Size = Size(440, 20)
        self.Controls.Add(lbl_subtitle)

        self.chk_list = CheckedListBox()
        self.chk_list.Font = Font("Segoe UI", 9)
        self.chk_list.Location = Point(20, 80)
        self.chk_list.Size = Size(440, 300)
        self.chk_list.CheckOnClick = True
        for ws in self.worksets:
            self.chk_list.Items.Add(ws.Name)
        # Tout coche par defaut : le cas d'usage courant est "une vue par
        # sous-projet, pour tous les sous-projets" - decocher est
        # l'exception, pas la regle.
        for i in range(self.chk_list.Items.Count):
            self.chk_list.SetItemChecked(i, True)
        self.Controls.Add(self.chk_list)

        # Type(s) de vue a creer : les deux coches par defaut (comportement
        # d'origine), decocher l'un des deux permet de ne creer que l'autre.
        self.chk_create_3d = CheckBox()
        self.chk_create_3d.Text = "Creer les vues 3D"
        self.chk_create_3d.Font = Font("Segoe UI", 9)
        self.chk_create_3d.Location = Point(20, 390)
        self.chk_create_3d.Size = Size(210, 24)
        self.chk_create_3d.Checked = True
        self.Controls.Add(self.chk_create_3d)

        self.chk_create_plan = CheckBox()
        self.chk_create_plan.Text = "Creer les plans (2D)"
        self.chk_create_plan.Font = Font("Segoe UI", 9)
        self.chk_create_plan.Location = Point(250, 390)
        self.chk_create_plan.Size = Size(210, 24)
        self.chk_create_plan.Checked = True
        self.Controls.Add(self.chk_create_plan)

        self.btn_select_all = Button()
        self.btn_select_all.Text = "Tout (des)selectionner"
        self.btn_select_all.Font = Font("Segoe UI", 9)
        self.btn_select_all.Size = Size(210, 35)
        self.btn_select_all.Location = Point(20, 420)
        self.btn_select_all.BackColor = Color.FromArgb(100, 100, 100)
        self.btn_select_all.ForeColor = Color.White
        self.btn_select_all.FlatStyle = FlatStyle.Flat
        self.btn_select_all.FlatAppearance.BorderSize = 0
        self.btn_select_all.Click += self.OnSelectAll
        self.Controls.Add(self.btn_select_all)

        self.btn_create = Button()
        self.btn_create.Text = "Creer les vues"
        self.btn_create.Font = Font("Segoe UI", 9, FontStyle.Bold)
        self.btn_create.Size = Size(210, 35)
        self.btn_create.Location = Point(250, 420)
        self.btn_create.BackColor = Color.FromArgb(0, 120, 215)
        self.btn_create.ForeColor = Color.White
        self.btn_create.FlatStyle = FlatStyle.Flat
        self.btn_create.FlatAppearance.BorderSize = 0
        self.btn_create.Click += self.OnCreate
        self.Controls.Add(self.btn_create)

        btn_close = Button()
        btn_close.Text = "Fermer"
        btn_close.Font = Font("Segoe UI", 9)
        btn_close.Size = Size(150, 30)
        btn_close.Location = Point(175, 470)
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

    def OnCreate(self, sender, event):
        checked_indices = list(self.chk_list.CheckedIndices)
        if not checked_indices:
            MessageBox.Show("Veuillez selectionner au moins un sous-projet.", "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        create_3d = self.chk_create_3d.Checked
        create_plan = self.chk_create_plan.Checked
        if not create_3d and not create_plan:
            MessageBox.Show("Veuillez cocher au moins un type de vue a creer (3D et/ou plan).", "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        selected_worksets = [self.worksets[i] for i in checked_indices]

        if create_3d and create_plan:
            types_desc = "vue(s) 3D + plan (2 vues"
        elif create_3d:
            types_desc = "vue(s) 3D (1 vue"
        else:
            types_desc = "plan(s) (1 vue"

        result = MessageBox.Show(
            "Creer " + types_desc + " par sous-projet coche, " + str(len(selected_worksets)) +
            " sous-projet(s)) ?\n\nLes vues deja existantes du meme nom ne sont pas recreees : "
            "seule la visibilite par sous-projet y est mise a jour (fermer tous les autres, ne "
            "laisser que celui de la vue).",
            "Confirmation",
            MessageBoxButtons.OKCancel,
            MessageBoxIcon.Question
        )
        if result != DialogResult.OK:
            return

        levels_sorted = get_niveaux_tries(doc) if create_plan else []
        base_level = levels_sorted[0] if levels_sorted else None

        created_3d = 0
        updated_3d = 0
        created_plan = 0
        updated_plan = 0
        errors = []

        t = RevitTransaction(doc, "Creation/mise a jour des vues par sous-projet")
        try:
            t.Start()
            for ws in selected_worksets:
                nom_base = VIEW_PREFIX + ws.Name
                nom_3d = nom_base + "_3D"
                nom_plan = nom_base + "_Plan"

                if create_3d:
                    try:
                        existing = find_existing_view(doc, nom_3d)
                        if existing is not None:
                            apply_workset_visibility(existing, ws, self.worksets)
                            updated_3d += 1
                        else:
                            new_view_3d, err = create_workset_3d_view(doc, ws, self.worksets, nom_3d, ZOOM_FACTOR)
                            if new_view_3d is None:
                                errors.append(nom_3d + " : " + str(err))
                            else:
                                created_3d += 1
                    except Exception as ex:
                        errors.append(nom_3d + " : " + str(ex))

                if create_plan:
                    if base_level is None:
                        errors.append(nom_plan + " : aucun niveau dans le document, plan non cree.")
                    else:
                        try:
                            existing = find_existing_view(doc, nom_plan)
                            if existing is not None:
                                apply_workset_visibility(existing, ws, self.worksets)
                                updated_plan += 1
                            else:
                                new_view_plan, err = create_workset_plan_view(
                                    doc, ws, self.worksets, nom_plan, base_level, levels_sorted, ZOOM_FACTOR)
                                if new_view_plan is None:
                                    errors.append(nom_plan + " : " + str(err))
                                else:
                                    created_plan += 1
                        except Exception as ex:
                            errors.append(nom_plan + " : " + str(ex))

            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            MessageBox.Show("Erreur globale : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)
            return

        self.selected_worksets = selected_worksets

        parts = []
        if create_3d:
            parts.append(str(created_3d) + " vue(s) 3D creee(s), " + str(updated_3d) + " mise(s) a jour")
        if create_plan:
            parts.append(str(created_plan) + " plan(s) cree(s), " + str(updated_plan) + " mis(e) a jour")

        message = (
            " ; ".join(parts) + "\n(sur " +
            str(len(selected_worksets)) + " sous-projet(s) selectionne(s))."
        )
        if errors:
            message += "\n\nEchecs :\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                message += "\n... et " + str(len(errors) - 10) + " autre(s)."

        MessageBox.Show(message, "Creation / Mise a jour terminee", MessageBoxButtons.OK,
                         MessageBoxIcon.Information if not errors else MessageBoxIcon.Warning)


# ============================================================
# EXECUTION
# ============================================================
if not doc.IsWorkshared:
    MessageBox.Show("Le document doit etre un fichier de travail partage (workshared) pour avoir des sous-projets.",
                     "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
    OUT = "Le document doit etre un fichier de travail partage (workshared)."
else:
    worksets = get_worksets_utilisateur(doc)
    if not worksets:
        MessageBox.Show("Aucun sous-projet utilisateur trouve dans ce document.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
        OUT = "Aucun sous-projet utilisateur trouve."
    else:
        try:
            form = WorksetViewsForm(worksets)
            form.ShowDialog()
            OUT = "Termine - " + str(len(form.selected_worksets)) + " sous-projet(s) traite(s) - 🎩 by Manseur Mohamed"
        except Exception as ex:
            # Sans ce MessageBox, une exception levee avant l'affichage de la
            # fenetre se terminerait en silence : Dynamo affiche "run
            # complete" sans que l'utilisateur ne voie ni interface ni
            # message d'erreur - impossible a diagnostiquer.
            try:
                MessageBox.Show("Erreur inattendue : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)
            except Exception:
                pass
            OUT = "Erreur inattendue : " + str(ex)
