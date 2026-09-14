# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Placer un Groupe de Modele par Piece
#
# Reprise en script Dynamo de la logique metier du plugin Revit BIMATIKA
# "Groupe par piece" (C:\...\BIMATIKA-GROUPEBYROOM\Command.cs) : place
# automatiquement un groupe de modele (mobilier, equipement...) dans
# toutes les pieces dont un parametre choisi vaut une valeur choisie
# (ex : toutes les pieces nommees "Chambre"), oriente selon la porte de
# chaque piece.
#
# La gestion de licence/essai du plugin d'origine (LicenseManager,
# ActivationForm) est propre a sa distribution commerciale independante et
# n'a pas de sens dans un script Dynamo interne : elle n'est pas reprise
# ici.
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

RevitTransaction = Transaction

from System.Windows.Forms import *
from System.Drawing import *

doc = DocumentManager.Instance.CurrentDBDocument

# Sens de rotation (-1 = horaire, repere Revit) et decalage (0/90/180/270
# degres) pour caler l'orientation du groupe sur la porte : la geometrie
# "avant" d'un groupe de modele et la convention "dedans/dehors" d'une
# famille de porte dependent de comment elles ont ete dessinees - il n'y a
# pas de reglage universel valable pour toutes les familles (le plugin
# BIMATIKA d'origine, dont ce script reprend la logique, le documente
# explicitement comme des constantes a calibrer au cas par cas). Exposes
# dans l'interface (case a cocher + liste deroulante) plutot qu'en dur,
# pour pouvoir les ajuster sans modifier le script.
AUCUNE_PORTE = "<Aucune - pas de rotation>"


# ============================================================
# COLLECTE : GROUPES DE MODELE / PIECES / PORTES
# ============================================================
def is_model_group_category(cat):
    try:
        return cat is not None and cat.Id is not None and int(cat.Id.IntegerValue) == int(BuiltInCategory.OST_IOSModelGroups)
    except Exception:
        return False


def safe_family_name(sym):
    try:
        return sym.Family.Name if sym.Family is not None else "Inconnu"
    except Exception:
        return "Inconnu"


def safe_type_name(sym):
    # .Name peut echouer (ou renvoyer vide) sur certains ElementType,
    # GroupType inclus - meme constat et meme repli deja utilises dans
    # Events-Local-MoMo (voir get_family_and_type) : si .Name ne donne
    # rien d'exploitable, on retombe sur les parametres integres qui
    # portent aussi le nom du type.
    try:
        n = sym.Name
        if n:
            return n
    except Exception:
        pass
    for bip_name in ("SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME"):
        try:
            bip = getattr(BuiltInParameter, bip_name, None)
            if bip is None:
                continue
            p = sym.get_Parameter(bip)
            if p is not None and p.HasValue:
                s = p.AsString()
                if s:
                    return s
        except Exception:
            continue
    return "Sans nom"


def collect_ids_safe(collector):
    """Recupere les ElementId d'un FilteredElementCollector sans jamais
    lever - ToElementIds() peut lever une InternalException en
    worksharing des qu'un seul element du lot echoue a se regenerer (cas
    frequent pour les Pieces non placees/redondantes) ; sans ce filet,
    UNE piece corrompue suffisait a faire echouer toute la collecte, et
    donc tout le script, AVANT meme l'affichage de l'interface - sans le
    moindre message a l'ecran (l'exception remontait jusqu'au bloc
    try/except final, qui ne montre pas de MessageBox)."""
    try:
        return list(collector.ToElementIds())
    except Exception:
        pass
    ids = []
    try:
        for e in collector:
            try:
                ids.append(e.Id)
            except Exception:
                continue
    except Exception:
        pass
    return ids


def get_model_group_types(doc):
    types = []
    for eid in collect_ids_safe(FilteredElementCollector(doc).OfClass(GroupType)):
        try:
            gt = doc.GetElement(eid)
            if gt is not None and is_model_group_category(gt.Category):
                types.append(gt)
        except Exception:
            continue
    try:
        return sorted(types, key=lambda gt: safe_type_name(gt))
    except Exception:
        return types


def get_placed_rooms(doc):
    """Pieces reellement placees (surface > 0 et point d'implantation
    valide) - exclut les pieces "non placees" (Area == 0, Location None)
    qui ne peuvent de toute facon pas recevoir de groupe."""
    rooms = []
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType()
    for eid in collect_ids_safe(collector):
        try:
            r = doc.GetElement(eid)
            if r is not None and r.Area > 0 and isinstance(r.Location, LocationPoint):
                rooms.append(r)
        except Exception:
            continue
    return rooms


def get_placed_doors(doc):
    doors = []
    collector = FilteredElementCollector(doc).OfClass(FamilyInstance).OfCategory(BuiltInCategory.OST_Doors)
    for eid in collect_ids_safe(collector):
        try:
            d = doc.GetElement(eid)
            if d is not None and d.Symbol is not None:
                doors.append(d)
        except Exception:
            continue
    return doors


def get_placed_door_types(doors):
    """Types de porte REELLEMENT PLACES (pas tous les types disponibles
    dans le projet) - inutile de proposer un type qu'aucune porte
    n'utilise, il ne permettrait jamais de trouver la porte d'une piece."""
    seen = {}
    for d in doors:
        try:
            sym = d.Symbol
            key = int(sym.Id.IntegerValue)
            if key not in seen:
                seen[key] = sym
        except Exception:
            continue
    return sorted(seen.values(), key=lambda s: (safe_family_name(s), safe_type_name(s)))


# ============================================================
# PARAMETRES DE PIECE
# ============================================================
def param_to_string(p):
    if p is None:
        return ""
    try:
        if p.StorageType == StorageType.String:
            return p.AsString() or ""
        elif p.StorageType == StorageType.Integer:
            v = p.AsValueString()
            return v if v else str(p.AsInteger())
        elif p.StorageType == StorageType.Double:
            v = p.AsValueString()
            return v if v else str(p.AsDouble())
        elif p.StorageType == StorageType.ElementId:
            v = p.AsValueString()
            return v if v else ""
    except Exception:
        pass
    return ""


def build_room_params(rooms):
    """Liste (triee) des parametres lisibles d'une piece, a partir de la
    premiere piece trouvee - les parametres de piece sont censes etre les
    memes d'une piece a l'autre dans un meme projet."""
    if not rooms:
        return []
    sample = rooms[0]
    result = []
    seen = set()
    try:
        for p in sample.Parameters:
            if p is None or p.Definition is None:
                continue
            nom = p.Definition.Name
            if not nom or nom in seen:
                continue
            seen.add(nom)
            result.append(nom)
    except Exception:
        pass
    return sorted(result)


def room_name_label(room):
    """Nom localise du parametre integre 'Nom' de piece (ex: 'Nom' en
    francais) - utilise comme parametre de classification par defaut."""
    try:
        p = room.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p is not None and p.Definition is not None:
            return p.Definition.Name
    except Exception:
        pass
    return "Nom"


def get_room_param_value(room, param_name):
    try:
        return param_to_string(room.LookupParameter(param_name))
    except Exception:
        return ""


# ============================================================
# ORIENTATION SELON LA PORTE
# ============================================================
def door_rooms(d):
    result = []
    try:
        fr = d.FromRoom
        if fr is not None:
            result.append(fr)
    except Exception:
        pass
    try:
        tr = d.ToRoom
        if tr is not None:
            result.append(tr)
    except Exception:
        pass
    return result


def cap_porte(facing):
    """Cap de la porte d'apres son sens (FacingOrientation) : Nord/Haut =
    0 degre, sens horaire."""
    ang = math.atan2(facing.X, facing.Y) * 180.0 / math.pi
    if ang < 0:
        ang += 360.0
    return ang


def get_door_point(d):
    try:
        loc = d.Location
        if loc is not None and hasattr(loc, "Point"):
            return loc.Point
    except Exception:
        pass
    return None


def find_nearest_door(room, doors_of_type):
    """Repli purement geometrique : la porte du type choisi la plus proche
    (distance horizontale) du point d'implantation de la piece.

    FromRoom/ToRoom (l'approche "officielle", utilisee en premier) depend
    du calcul de delimitement des pieces par Revit (Room Bounding), qui
    peut renvoyer systematiquement rien meme quand les portes et les
    pieces sont bien presentes et correctement placees dans le modele -
    c'est un defaut connu de cette API, pas forcement un probleme du
    projet. Ce repli ne depend d'aucun calcul prealable de Revit."""
    try:
        room_pt = room.Location.Point
    except Exception:
        return None

    best_door = None
    best_dist = None
    for d in doors_of_type:
        door_pt = get_door_point(d)
        if door_pt is None:
            continue
        dx = door_pt.X - room_pt.X
        dy = door_pt.Y - room_pt.Y
        dist = dx * dx + dy * dy  # comparaison seule : pas besoin de racine carree
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_door = d
    return best_door


# ============================================================
# INTERFACE (Windows Forms - meme charte que les autres scripts du projet)
# ============================================================
class PlacementForm(Form):
    def __init__(self, group_types, room_params, rooms, door_types, param_defaut):
        Form.__init__(self)
        self.group_types = group_types
        self.room_params = room_params
        self.rooms = rooms
        self.door_types = door_types
        self.param_defaut = param_defaut

        self.selected_group_type = None
        self.selected_param = None
        self.selected_value = None
        self.selected_door_type = None
        self.selected_offset = 0.0
        self.selected_signe = -1.0

        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "10 - Placer un Groupe de Modele par Piece - 🎩 by Manseur Mohamed"
        self.Size = Size(560, 495)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        lbl_title = Label()
        lbl_title.Text = "Placer un Groupe de Modele par Piece"
        lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
        lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
        lbl_title.Location = Point(20, 15)
        lbl_title.AutoSize = True
        self.Controls.Add(lbl_title)

        lbl_sig = Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_sig.Location = Point(340, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        lbl_subtitle = Label()
        lbl_subtitle.Text = "Place le groupe dans toutes les pieces dont le parametre choisi vaut la valeur choisie"
        lbl_subtitle.Font = Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = Point(20, 50)
        lbl_subtitle.Size = Size(510, 20)
        self.Controls.Add(lbl_subtitle)

        y = 85
        lbl_groupe = Label()
        lbl_groupe.Text = "Groupe de modele :"
        lbl_groupe.Font = Font("Segoe UI", 10)
        lbl_groupe.Location = Point(20, y)
        lbl_groupe.AutoSize = True
        self.Controls.Add(lbl_groupe)

        self.cmb_groupe = ComboBox()
        self.cmb_groupe.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_groupe.Font = Font("Segoe UI", 10)
        self.cmb_groupe.Location = Point(20, y + 25)
        self.cmb_groupe.Size = Size(510, 25)
        for gt in self.group_types:
            self.cmb_groupe.Items.Add(safe_type_name(gt))
        self.Controls.Add(self.cmb_groupe)

        y += 65
        lbl_param = Label()
        lbl_param.Text = "Parametre de piece :"
        lbl_param.Font = Font("Segoe UI", 10)
        lbl_param.Location = Point(20, y)
        lbl_param.AutoSize = True
        self.Controls.Add(lbl_param)

        self.cmb_param = ComboBox()
        self.cmb_param.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_param.Font = Font("Segoe UI", 10)
        self.cmb_param.Location = Point(20, y + 25)
        self.cmb_param.Size = Size(510, 25)
        for name in self.room_params:
            self.cmb_param.Items.Add(name)
        self.cmb_param.SelectedIndexChanged += self.OnParamChanged
        self.Controls.Add(self.cmb_param)

        y += 65
        lbl_valeur = Label()
        lbl_valeur.Text = "Valeur :"
        lbl_valeur.Font = Font("Segoe UI", 10)
        lbl_valeur.Location = Point(20, y)
        lbl_valeur.AutoSize = True
        self.Controls.Add(lbl_valeur)

        self.cmb_valeur = ComboBox()
        self.cmb_valeur.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_valeur.Font = Font("Segoe UI", 10)
        self.cmb_valeur.Location = Point(20, y + 25)
        self.cmb_valeur.Size = Size(510, 25)
        self.Controls.Add(self.cmb_valeur)

        y += 65
        lbl_porte = Label()
        lbl_porte.Text = "Type de porte (orientation) :"
        lbl_porte.Font = Font("Segoe UI", 10)
        lbl_porte.Location = Point(20, y)
        lbl_porte.AutoSize = True
        self.Controls.Add(lbl_porte)

        self.cmb_porte = ComboBox()
        self.cmb_porte.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_porte.Font = Font("Segoe UI", 10)
        self.cmb_porte.Location = Point(20, y + 25)
        self.cmb_porte.Size = Size(510, 25)
        self.cmb_porte.Items.Add(AUCUNE_PORTE)
        for sym in self.door_types:
            self.cmb_porte.Items.Add(safe_family_name(sym) + " : " + safe_type_name(sym))
        # Type de porte preselectionne si au moins un type est place dans
        # le projet (index 0 = AUCUNE_PORTE, index 1 = premier type reel).
        self.cmb_porte.SelectedIndex = 1 if len(self.door_types) > 0 else 0
        self.Controls.Add(self.cmb_porte)

        y += 65
        lbl_calib = Label()
        lbl_calib.Text = "Calibrage de l'orientation (si le sens ne correspond pas) :"
        lbl_calib.Font = Font("Segoe UI", 9, FontStyle.Italic)
        lbl_calib.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_calib.Location = Point(20, y)
        lbl_calib.AutoSize = True
        self.Controls.Add(lbl_calib)

        y += 25
        self.cmb_offset = ComboBox()
        self.cmb_offset.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_offset.Font = Font("Segoe UI", 9)
        self.cmb_offset.Location = Point(20, y)
        self.cmb_offset.Size = Size(150, 25)
        for label in ("Decalage 0", "Decalage 90", "Decalage 180", "Decalage 270"):
            self.cmb_offset.Items.Add(label)
        self.cmb_offset.SelectedIndex = 0
        self.Controls.Add(self.cmb_offset)

        self.chk_invert = CheckBox()
        self.chk_invert.Text = "Inverser le sens de rotation"
        self.chk_invert.Font = Font("Segoe UI", 9)
        self.chk_invert.Location = Point(190, y + 3)
        self.chk_invert.Size = Size(340, 24)
        self.chk_invert.Checked = True  # correspond a SIGNE = -1 (comportement par defaut du plugin d'origine)
        self.Controls.Add(self.chk_invert)

        y += 65
        self.btn_ok = Button()
        self.btn_ok.Text = "Placer les groupes"
        self.btn_ok.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.btn_ok.Size = Size(250, 40)
        self.btn_ok.Location = Point(20, y)
        self.btn_ok.BackColor = Color.FromArgb(0, 120, 215)
        self.btn_ok.ForeColor = Color.White
        self.btn_ok.FlatStyle = FlatStyle.Flat
        self.btn_ok.FlatAppearance.BorderSize = 0
        self.btn_ok.Click += self.OnOk
        self.Controls.Add(self.btn_ok)

        self.btn_cancel = Button()
        self.btn_cancel.Text = "Annuler"
        self.btn_cancel.Font = Font("Segoe UI", 10)
        self.btn_cancel.Size = Size(250, 40)
        self.btn_cancel.Location = Point(280, y)
        self.btn_cancel.BackColor = Color.FromArgb(100, 100, 100)
        self.btn_cancel.ForeColor = Color.White
        self.btn_cancel.FlatStyle = FlatStyle.Flat
        self.btn_cancel.FlatAppearance.BorderSize = 0
        self.btn_cancel.Click += self.OnCancel
        self.Controls.Add(self.btn_cancel)

        # Selection par defaut : premier groupe, parametre "Nom" (ou
        # premier disponible), puis on peuple la liste des valeurs.
        if self.cmb_groupe.Items.Count > 0:
            self.cmb_groupe.SelectedIndex = 0

        idx = -1
        try:
            idx = self.room_params.index(self.param_defaut)
        except ValueError:
            idx = 0 if self.room_params else -1
        if idx >= 0:
            self.cmb_param.SelectedIndex = idx
        self.RefreshValues()

    def RefreshValues(self):
        self.cmb_valeur.Items.Clear()
        idx = self.cmb_param.SelectedIndex
        if idx < 0:
            return
        param_name = self.room_params[idx]
        valeurs = sorted(set(v for v in (get_room_param_value(r, param_name) for r in self.rooms) if v))
        for v in valeurs:
            self.cmb_valeur.Items.Add(v)
        if self.cmb_valeur.Items.Count > 0:
            self.cmb_valeur.SelectedIndex = 0

    def OnParamChanged(self, sender, event):
        self.RefreshValues()

    def OnOk(self, sender, event):
        if self.cmb_groupe.SelectedIndex < 0 or self.cmb_param.SelectedIndex < 0 or self.cmb_valeur.SelectedIndex < 0:
            MessageBox.Show("Veuillez renseigner le groupe, le parametre et la valeur.", "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        self.selected_group_type = self.group_types[self.cmb_groupe.SelectedIndex]
        self.selected_param = self.room_params[self.cmb_param.SelectedIndex]
        self.selected_value = self.cmb_valeur.SelectedItem

        porte_idx = self.cmb_porte.SelectedIndex
        self.selected_door_type = self.door_types[porte_idx - 1] if porte_idx > 0 else None

        self.selected_offset = float(self.cmb_offset.SelectedIndex * 90)
        self.selected_signe = -1.0 if self.chk_invert.Checked else 1.0

        self.DialogResult = DialogResult.OK
        self.Close()

    def OnCancel(self, sender, event):
        self.DialogResult = DialogResult.Cancel
        self.Close()


# ============================================================
# FONCTION PRINCIPALE
# ============================================================
def main():
    group_types = get_model_group_types(doc)
    if not group_types:
        MessageBox.Show("Aucun groupe de modele dans le projet.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
        return {"success": False, "message": "Aucun groupe de modele dans le projet."}

    rooms = get_placed_rooms(doc)
    if not rooms:
        MessageBox.Show("Aucune piece placee dans le projet.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
        return {"success": False, "message": "Aucune piece placee dans le projet."}

    room_params = build_room_params(rooms)
    if not room_params:
        MessageBox.Show("Aucun parametre lisible trouve sur les pieces.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
        return {"success": False, "message": "Aucun parametre de piece disponible."}

    all_doors = get_placed_doors(doc)
    door_types = get_placed_door_types(all_doors)
    param_defaut = room_name_label(rooms[0])

    form = PlacementForm(group_types, room_params, rooms, door_types, param_defaut)
    if form.ShowDialog() != DialogResult.OK:
        return {"success": False, "message": "Operation annulee par l'utilisateur."}

    gt = form.selected_group_type
    param_name = form.selected_param
    valeur = form.selected_value
    door_type = form.selected_door_type
    signe = form.selected_signe
    offset = form.selected_offset

    cibles = [r for r in rooms if get_room_param_value(r, param_name) == valeur]
    cible_ids = set(int(r.Id.IntegerValue) for r in cibles)

    # Porte par piece cible (indexee une seule fois, sur les portes du
    # type choisi uniquement) : on s'arrete des que toutes les pieces
    # cibles ont trouve leur porte, inutile de continuer a scanner le
    # reste du batiment une fois le resultat complet.
    porte_par_piece = {}
    doors_of_type = []
    if door_type is not None:
        for d in all_doors:
            try:
                if int(d.Symbol.Id.IntegerValue) != int(door_type.Id.IntegerValue):
                    continue
            except Exception:
                continue
            doors_of_type.append(d)

        # 1) Methode "officielle" : FromRoom/ToRoom (delimitement des
        # pieces calcule par Revit).
        for d in doors_of_type:
            if len(porte_par_piece) >= len(cible_ids):
                break
            for rm in door_rooms(d):
                try:
                    rm_id = int(rm.Id.IntegerValue)
                except Exception:
                    continue
                if rm_id in cible_ids and rm_id not in porte_par_piece:
                    porte_par_piece[rm_id] = d

        # 2) Repli geometrique pour les pieces non resolues par
        # FromRoom/ToRoom (voir find_nearest_door : ce calcul de Revit
        # peut ne rien renvoyer meme quand tout est bien modelise).
        for room in cibles:
            try:
                room_id = int(room.Id.IntegerValue)
            except Exception:
                continue
            if room_id in porte_par_piece:
                continue
            nearest = find_nearest_door(room, doors_of_type)
            if nearest is not None:
                porte_par_piece[room_id] = nearest

    places = 0
    sans_porte = 0

    t = RevitTransaction(doc, "Placer groupes par parametre de piece")
    try:
        t.Start()

        # Creation et rotation separees, avec UNE SEULE regeneration entre
        # les deux : un groupe fraichement cree n'a pas de geometrie/
        # transform valide tant que le document n'a pas ete regenere.
        # Appeler RotateElement juste apres chaque PlaceGroup forcerait
        # une regeneration complete a CHAQUE iteration (O(N) regenerations
        # pour N pieces) au lieu d'une seule pour tout le lot.
        placees_avec_groupe = []
        groupe_ids = []

        for room in cibles:
            try:
                room_pt = room.Location.Point
                g = doc.Create.PlaceGroup(room_pt, gt)
                places += 1
                placees_avec_groupe.append(room)
                groupe_ids.append(g.Id)
            except Exception:
                continue

        doc.Regenerate()

        for i in range(len(placees_avec_groupe)):
            room = placees_avec_groupe[i]
            try:
                room_id = int(room.Id.IntegerValue)
            except Exception:
                room_id = None

            porte = porte_par_piece.get(room_id) if room_id is not None else None
            if porte is not None:
                try:
                    cap_d = cap_porte(porte.FacingOrientation)
                    rot = signe * (cap_d + offset)
                    rot = ((rot % 360.0) + 360.0) % 360.0

                    if rot > 0.01 and rot < 359.99:
                        room_pt = room.Location.Point
                        axe = Line.CreateBound(room_pt, XYZ(room_pt.X, room_pt.Y, room_pt.Z + 1.0))
                        ElementTransformUtils.RotateElement(doc, groupe_ids[i], axe, rot * math.pi / 180.0)
                except Exception:
                    pass
            else:
                sans_porte += 1

        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        MessageBox.Show("Erreur : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)
        return {"success": False, "message": "Erreur : " + str(ex)}

    message = (
        param_name + " = " + valeur + "\n" +
        "Groupes places : " + str(places) + "\n" +
        "Pieces sans porte du type choisi : " + str(sans_porte)
    )
    MessageBox.Show(message, "Placer Groupe par Piece", MessageBoxButtons.OK, MessageBoxIcon.Information)

    return {
        "success": True,
        "message": message,
        "parametre": param_name,
        "valeur": valeur,
        "groupes_places": places,
        "pieces_sans_porte": sans_porte
    }


# ============================================================
# EXECUTION
# ============================================================
try:
    OUT = main()
except Exception as ex:
    # Sans ce MessageBox, une exception levee avant le premier appel a une
    # fenetre/MessageBox a l'interieur de main() se serait terminee ici en
    # silence : Dynamo affiche "run complete" (OUT est bien assigne, aucune
    # exception Python ne remonte au moteur du noeud), mais l'utilisateur
    # ne voit ni interface ni message d'erreur - impossible a diagnostiquer.
    try:
        MessageBox.Show("Erreur inattendue : " + str(ex), "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)
    except Exception:
        pass
    OUT = {"success": False, "message": "Erreur inattendue : " + str(ex)}
