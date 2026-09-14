# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Creation automatique de vues par utilisateur
# CORRIGE - Gestion des bounding box invalides (NaN/Infini)
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

RevitView = View
RevitTransaction = Transaction

from System.Windows.Forms import *
from System.Drawing import *
from System.Collections.Generic import List

# ============================================================
# CONFIGURATION
# ============================================================
doc = DocumentManager.Instance.CurrentDBDocument
facteur_zoom = 1.3
logs = []


def id_value(element_id):
    # ElementId.IntegerValue est obsolete depuis Revit 2024 et supprime dans
    # les versions recentes, remplace par ElementId.Value. On teste la
    # presence de la nouvelle propriete pour rester compatible 2024-2027.
    if hasattr(element_id, "Value"):
        return element_id.Value
    return element_id.IntegerValue

def log(msg):
    logs.append(msg)

# ============================================================
# COLLECTE DES WARNINGS PAR PERSONNE
# ============================================================
def get_person(element_id, mode="creator"):
    try:
        info = WorksharingUtils.GetWorksharingTooltipInfo(doc, element_id)
        if info:
            if mode == "creator" and info.Creator:
                return info.Creator
            elif mode == "last_changed_by" and info.LastChangedBy:
                return info.LastChangedBy
    except Exception:
        pass
    return "Inconnu"

def collect_warnings_by_person(mode="creator"):
    data = {}
    warnings = doc.GetWarnings()
    log("Nombre total de warnings : " + str(warnings.Count))

    for w in warnings:
        failing_ids = w.GetFailingElements()
        for eid in failing_ids:
            elem = doc.GetElement(eid)
            if elem is None:
                continue

            person = get_person(eid, mode)

            if person not in data:
                data[person] = []

            eid_int = int(id_value(eid))
            if not any(int(id_value(existing)) == eid_int for existing in data[person]):
                data[person].append(eid)

    log("Personnes trouvees : " + str(len(data)))
    for p, elems in data.items():
        log("  - " + p + " : " + str(len(elems)) + " element(s)")
    return data

# ============================================================
# OUTILS VUE 3D / MASQUAGE PERMANENT
# ============================================================
INVALID_VIEW_NAME_CHARS = set('\\:{}[]|;<>?`~')

def sanitize_view_name(text):
    return "".join((c if c not in INVALID_VIEW_NAME_CHARS else "_") for c in text)

def delete_existing_view(view_name):
    # On MATERIALISE d'abord la liste des vues a supprimer (.ToElements())
    # avant tout doc.Delete() : supprimer un element pendant qu'on itere
    # directement sur un FilteredElementCollector modifie la collection en
    # cours de parcours, ce qui peut faire lever une exception a l'iterateur
    # (hors du try/except interne, donc fatale pour tout le script).
    to_delete = []
    for v in FilteredElementCollector(doc).OfClass(RevitView).ToElements():
        try:
            if not v.IsTemplate and v.Name == view_name:
                to_delete.append(v.Id)
        except Exception:
            pass

    deleted = False
    for vid in to_delete:
        try:
            doc.Delete(vid)
            deleted = True
        except Exception:
            pass
    return deleted

def is_finite(value):
    """Verifie qu'une valeur est finie (pas NaN, pas infini)."""
    try:
        return not (math.isnan(value) or math.isinf(value))
    except:
        return False

def get_bbox_for_elements(element_ids):
    min_pt = None
    max_pt = None
    found = False

    for eid in element_ids:
        elem = doc.GetElement(eid)
        if elem is None:
            continue
        try:
            bb = elem.get_BoundingBox(None)
            if bb is not None:
                # Verifier que les coordonnees sont valides
                if (is_finite(bb.Min.X) and is_finite(bb.Min.Y) and is_finite(bb.Min.Z) and
                    is_finite(bb.Max.X) and is_finite(bb.Max.Y) and is_finite(bb.Max.Z)):
                    
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

def create_isolated_3d_view(view_name, element_ids, zoom_factor):
    # Trouver le type de vue 3D
    vft_3d = None
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if vft.ViewFamily == ViewFamily.ThreeDimensional:
            vft_3d = vft
            break
    if vft_3d is None:
        return None, "Aucun type de vue 3D disponible dans ce document.", 0

    # Creer la vue
    new_view = View3D.CreateIsometric(doc, vft_3d.Id)
    new_view.Name = view_name

    target_ints = set(int(id_value(eid)) for eid in element_ids)

    # Cadrage avec validation
    min_pt, max_pt = get_bbox_for_elements(element_ids)
    if min_pt is not None and max_pt is not None:
        # Verifier que la taille est raisonnable
        size_x = max_pt.X - min_pt.X
        size_y = max_pt.Y - min_pt.Y
        size_z = max_pt.Z - min_pt.Z
        
        if is_finite(size_x) and is_finite(size_y) and is_finite(size_z) and size_x > 0 and size_y > 0 and size_z > 0:
            center = XYZ((min_pt.X + max_pt.X) / 2.0, (min_pt.Y + max_pt.Y) / 2.0, (min_pt.Z + max_pt.Z) / 2.0)
            size_x = max(size_x * zoom_factor, 5.0)
            size_y = max(size_y * zoom_factor, 5.0)
            size_z = max(size_z * zoom_factor, 5.0)

            section_box = BoundingBoxXYZ()
            section_box.Min = XYZ(center.X - size_x / 2.0, center.Y - size_y / 2.0, center.Z - size_z / 2.0)
            section_box.Max = XYZ(center.X + size_x / 2.0, center.Y + size_y / 2.0, center.Z + size_z / 2.0)
            
            # Verifier une derniere fois que la section box est valide
            if (is_finite(section_box.Min.X) and is_finite(section_box.Max.X) and
                is_finite(section_box.Min.Y) and is_finite(section_box.Max.Y) and
                is_finite(section_box.Min.Z) and is_finite(section_box.Max.Z)):
                try:
                    new_view.SetSectionBox(section_box)
                    new_view.IsSectionBoxActive = True
                except Exception as ex:
                    log("  Warning cadrage : " + str(ex))
        else:
            log("  Warning : bounding box de taille invalide, pas de cadrage")
    else:
        log("  Warning : aucun element avec bounding box valide, pas de cadrage")

    # Masquage permanent
    #
    # doc.Regenerate() est indispensable ici : la vue vient d'etre creee (et
    # sa section box vient d'etre posee) dans la MEME transaction. Sans
    # regeneration, un FilteredElementCollector limite a new_view.Id peut ne
    # rien renvoyer du tout - on n'aurait alors rien masque et l'isolement
    # permanent serait silencieusement vide.
    try:
        doc.Regenerate()
    except Exception:
        pass

    other_ids = List[ElementId]()
    for elem in FilteredElementCollector(doc, new_view.Id).WhereElementIsNotElementType():
        try:
            if int(id_value(elem.Id)) in target_ints:
                continue
            if elem.CanBeHidden(new_view):
                other_ids.Add(elem.Id)
        except Exception:
            continue

    hidden_count = 0
    warning_msg = None
    if other_ids.Count > 0:
        try:
            new_view.HideElements(other_ids)
            hidden_count = other_ids.Count
        except Exception as ex:
            warning_msg = "Masquage partiel : " + str(ex)

    return new_view, warning_msg, hidden_count

# ============================================================
# INTERFACE MINIMALE
# ============================================================
class AutoViewForm(Form):
    def __init__(self):
        Form.__init__(self)
        self.mode = "creator"
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "03 - Isolement Permanent par Createur et Warning - 🎩 by Manseur Mohamed"
        self.Size = Size(500, 320)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        lbl_title = Label()
        lbl_title.Text = "Vues 3D par utilisateur"
        lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
        lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_title.Location = Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        lbl_sig = Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_sig.Location = Point(330, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        lbl_mode = Label()
        lbl_mode.Text = "Creer une vue 3D par utilisateur pour :"
        lbl_mode.Font = Font("Segoe UI", 10)
        lbl_mode.Location = Point(20, 60)
        lbl_mode.AutoSize = True
        self.Controls.Add(lbl_mode)

        self.cmb_mode = ComboBox()
        self.cmb_mode.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_mode.Font = Font("Segoe UI", 10)
        self.cmb_mode.Location = Point(20, 85)
        self.cmb_mode.Size = Size(450, 25)
        self.cmb_mode.Items.Add("Createur (auteur original)")
        self.cmb_mode.Items.Add("Dernier modificateur")
        self.cmb_mode.Items.Add("Les deux (createur + modificateur)")
        self.cmb_mode.SelectedIndex = 0
        self.Controls.Add(self.cmb_mode)

        lbl_info = Label()
        lbl_info.Text = "Une vue 3D sera creee par utilisateur avec TOUS ses warnings\r\n(isolement permanent dans le fichier)."
        lbl_info.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_info.ForeColor = Color.FromArgb(80, 80, 80)
        lbl_info.Location = Point(20, 125)
        lbl_info.Size = Size(450, 40)
        self.Controls.Add(lbl_info)

        self.btn_create = Button()
        self.btn_create.Text = "Creer les vues automatiquement"
        self.btn_create.Font = Font("Segoe UI", 11, FontStyle.Bold)
        self.btn_create.Size = Size(450, 50)
        self.btn_create.Location = Point(20, 175)
        self.btn_create.BackColor = Color.FromArgb(0, 120, 215)
        self.btn_create.ForeColor = Color.White
        self.btn_create.FlatStyle = FlatStyle.Flat
        self.btn_create.FlatAppearance.BorderSize = 0
        self.btn_create.Click += self.OnCreate
        self.Controls.Add(self.btn_create)

        self.btn_cancel = Button()
        self.btn_cancel.Text = "Annuler"
        self.btn_cancel.Font = Font("Segoe UI", 10)
        self.btn_cancel.Size = Size(450, 35)
        self.btn_cancel.Location = Point(20, 235)
        self.btn_cancel.BackColor = Color.FromArgb(100, 100, 100)
        self.btn_cancel.ForeColor = Color.White
        self.btn_cancel.FlatStyle = FlatStyle.Flat
        self.btn_cancel.FlatAppearance.BorderSize = 0
        self.btn_cancel.Click += self.OnCancel
        self.Controls.Add(self.btn_cancel)

    def OnCreate(self, sender, event):
        idx = self.cmb_mode.SelectedIndex
        if idx == 0:
            self.mode = "creator"
        elif idx == 1:
            self.mode = "last_changed_by"
        else:
            self.mode = "both"
        self.DialogResult = DialogResult.OK
        self.Close()

    def OnCancel(self, sender, event):
        self.DialogResult = DialogResult.Cancel
        self.Close()

# ============================================================
# FONCTION PRINCIPALE
# ============================================================
def main():
    if doc.GetWarnings().Count == 0:
        MessageBox.Show("Aucun warning trouve dans ce document.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
        return {"success": False, "message": "Aucun warning."}

    form = AutoViewForm()
    if form.ShowDialog() != DialogResult.OK:
        return {"success": False, "message": "Operation annulee."}

    mode = form.mode
    results = []
    total_views = 0
    total_errors = 0

    modes_to_process = []
    if mode == "creator":
        modes_to_process = [("creator", "CREATEUR")]
    elif mode == "last_changed_by":
        modes_to_process = [("last_changed_by", "MODIFICATEUR")]
    else:
        modes_to_process = [("creator", "CREATEUR"), ("last_changed_by", "MODIFICATEUR")]

    for current_mode, prefix in modes_to_process:
        log("--- Mode : " + prefix + " ---")
        data_by_person = collect_warnings_by_person(current_mode)

        if not data_by_person:
            log("Aucune donnee pour ce mode.")
            continue

        for person, element_ids in data_by_person.items():
            view_name = sanitize_view_name("WARN_{}_{}".format(prefix, person))
            if len(view_name) > 180:
                view_name = view_name[:180]

            log("Traitement : " + view_name + " (" + str(len(element_ids)) + " elements)")

            t = RevitTransaction(doc, "Vue " + view_name)
            try:
                t.Start()
                delete_existing_view(view_name)
                new_view, warn_msg, hidden_count = create_isolated_3d_view(view_name, element_ids, facteur_zoom)

                if new_view is not None:
                    t.Commit()
                    results.append({
                        "view": view_name,
                        "person": person,
                        "mode": prefix,
                        "elements": len(element_ids),
                        "hidden": hidden_count
                    })
                    total_views += 1
                    log("  -> Vue creee avec succes")
                else:
                    t.RollBack()
                    total_errors += 1
                    results.append({"error": view_name + " : " + str(warn_msg)})
                    log("  -> ERREUR : " + str(warn_msg))
            except Exception as ex:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                total_errors += 1
                results.append({"error": view_name + " : " + str(ex)})
                log("  -> EXCEPTION : " + str(ex))

    message = "Termine !\n\n" + str(total_views) + " vue(s) creee(s) avec isolement permanent."
    if total_errors > 0:
        message += "\n" + str(total_errors) + " erreur(s)."
    message += "\n\nVues creees :\n"
    for r in results:
        if "error" not in r:
            message += "- " + r["view"] + " [" + r["person"] + "] : " + str(r["elements"]) + " element(s)\n"

    if total_errors > 0:
        message += "\nErreurs :\n"
        for r in results:
            if "error" in r:
                message += "- " + r["error"] + "\n"

    MessageBox.Show(message, "Resultat", MessageBoxButtons.OK, 
                    MessageBoxIcon.Information if total_views > 0 else MessageBoxIcon.Warning)

    return {
        "success": total_views > 0,
        "total_views": total_views,
        "total_errors": total_errors,
        "logs": logs,
        "views": results
    }

# ============================================================
# EXECUTION
# ============================================================
try:
    OUT = main()
except Exception as ex:
    MessageBox.Show("Erreur inattendue : " + str(ex) + "\n\nLogs :\n" + "\n".join(logs), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)
    OUT = {"success": False, "message": "Erreur : " + str(ex), "logs": logs}