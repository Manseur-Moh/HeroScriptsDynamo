# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Gestion des Types Miroir (assistant en 3 etapes)
#
# Fusionne les anciens scripts "05 - Isoler et Lister les Types Miroir" et
# "06 - Creer des Types et Remplacer les Miroirs" en UN seul outil, sous
# forme d'assistant a 3 FENETRES SUCCESSIVES (et non plus une seule fenetre
# a boutons sequentiels) :
#   1. Liste des types ayant des instances miroir - cases a cocher pour
#      choisir ceux a traiter (option : isoler ces elements dans la vue
#      active pour les visualiser avant de continuer).
#   2. Prefixe / suffixe pour nommer les nouveaux types, puis creation
#      (ou recuperation si un type de ce nom existe deja) dans la maquette.
#   3. Remplacement des elements miroir des types traites par leurs
#      nouveaux types.
#
# Chaque etape peut etre interrompue sans casser les etapes precedentes
# deja realisees : annuler a l'etape 1 n'a rien modifie ; s'arreter apres
# l'etape 2 laisse les nouveaux types crees mais aucun element remplace.
# ============================================================

import clr

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
from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument
uidoc = DocumentManager.Instance.CurrentUIApplication.ActiveUIDocument


def id_value(element_id):
    # ElementId.IntegerValue est obsolete depuis Revit 2024 et supprime dans
    # les versions recentes, remplace par ElementId.Value. On teste la
    # presence de la nouvelle propriete pour rester compatible 2024-2027.
    if hasattr(element_id, "Value"):
        return element_id.Value
    return element_id.IntegerValue


# ============================================================
# COLLECTE DES ELEMENTS / TYPES MIROIR
# ============================================================
def get_mirrored_instances():
    collector = FilteredElementCollector(doc).OfClass(FamilyInstance)
    # try/except PAR ELEMENT : .Mirrored peut lever une exception sur
    # certaines instances (familles in-situ, geometrie non resolue). Sans ce
    # garde-fou, un seul element defaillant faisait echouer toute la collecte.
    result = []
    for e in collector:
        try:
            if e.Mirrored:
                result.append(e)
        except Exception:
            continue
    return result


def group_by_symbol(mirrored_instances):
    """dict[int Symbol.Id] = {"symbol": FamilySymbol, "count": int}"""
    groups = {}
    for e in mirrored_instances:
        sym = e.Symbol
        key = int(id_value(sym.Id))
        if key not in groups:
            groups[key] = {"symbol": sym, "count": 0}
        groups[key]["count"] += 1
    return groups


def safe_family_name(sym):
    # hasattr(sym, "Family") ne verifierait que l'EXISTENCE de la
    # propriete, pas qu'elle renvoie une valeur non-nulle : pour certains
    # symboles (famille imbriquee, symbole systeme...), sym.Family peut
    # valoir None, et .Family.Name planterait alors avec une
    # AttributeError qui interromprait tout le chargement a cause d'un
    # seul element.
    try:
        if sym.Family is not None:
            return sym.Family.Name
    except Exception:
        pass
    return "Inconnu"


def safe_type_name(sym):
    try:
        return sym.Name
    except Exception:
        return "Sans nom"


# ============================================================
# ETAPE 2 : CREATION / RECUPERATION DES TYPES
# ============================================================
def same_family(el, original_sym):
    """Les deux types appartiennent-ils a la meme famille ?

    Les noms de type ne sont uniques QU'AU SEIN d'une famille : deux familles
    differentes peuvent parfaitement avoir chacune un type "X_Miroir". Sans
    ce controle, find_or_create_type pouvait renvoyer le type homonyme d'une
    AUTRE famille et l'etape 3 affectait alors aux elements un type qui n'a
    rien a voir avec leur famille d'origine (ou echouait sur param.Set)."""
    try:
        fam_new = el.Family
        fam_ref = original_sym.Family
        if fam_new is None or fam_ref is None:
            # Types systeme (murs, sols...) : pas de notion de famille
            # chargeable, le nom est unique dans le document, on accepte.
            return True
        return int(id_value(fam_new.Id)) == int(id_value(fam_ref.Id))
    except Exception:
        return True


def find_or_create_type(original_sym, new_name, log):
    try:
        collector = FilteredElementCollector(doc).OfClass(original_sym.GetType())
        for el in collector:
            # try/except PAR ELEMENT : si un element du collector plante
            # sur .Name, on continue avec les suivants plutot que
            # d'abandonner toute la recherche.
            try:
                if el.Name == new_name and same_family(el, original_sym):
                    log("Type '" + new_name + "' existe deja (Id: " + str(el.Id) + ")")
                    return el
            except Exception:
                continue
    except Exception:
        pass

    try:
        new_sym = original_sym.Duplicate(new_name)
        log("Type cree : '" + new_name + "' (Id: " + str(new_sym.Id) + ")")
        return new_sym
    except Exception as ex:
        log("Erreur creation '" + new_name + "' : " + str(ex))
        return None


# ============================================================
# ETAPE 3 : REMPLACEMENT DES ELEMENTS
# ============================================================
def replace_elements(type_mapping, log):
    mirrored_instances = get_mirrored_instances()
    replaced_count = 0
    skipped_count = 0

    for elem in mirrored_instances:
        old_sym_id = int(id_value(elem.Symbol.Id))
        if old_sym_id not in type_mapping:
            continue

        new_type = type_mapping[old_sym_id]
        if elem.Symbol.Id == new_type.Id:
            skipped_count += 1
            continue

        try:
            param = elem.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
            if param is not None and not param.IsReadOnly:
                param.Set(new_type.Id)
                replaced_count += 1
            else:
                log("Parametre en lecture seule pour element Id: " + str(elem.Id))
                skipped_count += 1
        except Exception as ex:
            log("Erreur remplacement element Id " + str(elem.Id) + " : " + str(ex))
            skipped_count += 1

    return replaced_count, skipped_count


# ============================================================
# STYLE COMMUN AUX 3 FENETRES (meme charte que les autres scripts du
# projet : Segoe UI, fond gris clair, titre en haut a gauche, signature en
# haut a droite)
# ============================================================
FORM_WIDTH = 520


def add_header(form, step_title):
    lbl_title = Label()
    lbl_title.Text = step_title
    lbl_title.Font = Font("Segoe UI", 14, FontStyle.Bold)
    lbl_title.ForeColor = Color.FromArgb(50, 50, 50)
    lbl_title.Location = Point(20, 15)
    lbl_title.AutoSize = True
    form.Controls.Add(lbl_title)

    lbl_sig = Label()
    lbl_sig.Text = "🎩 by Manseur Mohamed"
    lbl_sig.Font = Font("Segoe UI", 9, FontStyle.Italic)
    lbl_sig.ForeColor = Color.FromArgb(100, 100, 100)
    # FORM_WIDTH - 190 = x 330 pour une fenetre de 520 : la signature (~140px
    # de large en 9pt italique) se termine vers 470, soit juste avant le bord
    # de la zone client (~504px), et commence bien apres les titres d'etape
    # (le plus long, "Etape 3/3 - Remplacement", s'arrete vers x=267 en 14pt
    # gras). L'ancienne valeur -220 collait la signature a x=300 et les
    # titres d'etape, alors plus longs, passaient DESSOUS.
    lbl_sig.Location = Point(FORM_WIDTH - 190, 20)
    lbl_sig.AutoSize = True
    form.Controls.Add(lbl_sig)


def style_primary(btn):
    btn.BackColor = Color.FromArgb(0, 120, 215)
    btn.ForeColor = Color.White
    btn.FlatStyle = FlatStyle.Flat
    btn.FlatAppearance.BorderSize = 0


def style_success(btn):
    btn.BackColor = Color.FromArgb(16, 124, 16)
    btn.ForeColor = Color.White
    btn.FlatStyle = FlatStyle.Flat
    btn.FlatAppearance.BorderSize = 0


def style_warning_action(btn):
    btn.BackColor = Color.FromArgb(200, 80, 0)
    btn.ForeColor = Color.White
    btn.FlatStyle = FlatStyle.Flat
    btn.FlatAppearance.BorderSize = 0


def style_secondary(btn):
    btn.BackColor = Color.FromArgb(100, 100, 100)
    btn.ForeColor = Color.White
    btn.FlatStyle = FlatStyle.Flat
    btn.FlatAppearance.BorderSize = 0


# ============================================================
# ETAPE 1 : SELECTION DES TYPES
# ============================================================
class SelectTypesForm(Form):
    def __init__(self, groups):
        Form.__init__(self)
        self.groups = groups
        self.ordered_keys = sorted(groups.keys(), key=lambda k: groups[k]["count"], reverse=True)
        self.selected_symbols = []
        self.isolate_view = False
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "05 - Gestion des Types Miroir (Etape 1/3 - Selection) - 🎩 by Manseur Mohamed"
        self.Size = Size(FORM_WIDTH, 600)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        add_header(self, "Etape 1/3 - Selection")

        lbl_subtitle = Label()
        lbl_subtitle.Text = str(len(self.ordered_keys)) + " type(s) avec instance(s) miroir - cochez ceux a traiter"
        lbl_subtitle.Font = Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = Point(20, 50)
        lbl_subtitle.AutoSize = True
        self.Controls.Add(lbl_subtitle)

        self.chk_list = CheckedListBox()
        self.chk_list.Font = Font("Segoe UI", 9)
        self.chk_list.Location = Point(20, 80)
        self.chk_list.Size = Size(460, 300)
        self.chk_list.CheckOnClick = True
        for key in self.ordered_keys:
            info = self.groups[key]
            sym = info["symbol"]
            count = info["count"]
            suffix = "s" if count > 1 else ""
            display = safe_family_name(sym) + " - " + safe_type_name(sym) + " (" + str(count) + " instance" + suffix + ")"
            self.chk_list.Items.Add(display)
        self.Controls.Add(self.chk_list)

        self.btn_select_all = Button()
        self.btn_select_all.Text = "Tout (des)selectionner"
        self.btn_select_all.Font = Font("Segoe UI", 9)
        self.btn_select_all.Size = Size(220, 32)
        self.btn_select_all.Location = Point(20, 390)
        style_secondary(self.btn_select_all)
        self.btn_select_all.Click += self.OnSelectAll
        self.Controls.Add(self.btn_select_all)

        self.chk_isolate = CheckBox()
        self.chk_isolate.Text = "Isoler ces elements dans la vue active (temporaire)"
        self.chk_isolate.Font = Font("Segoe UI", 9)
        self.chk_isolate.Location = Point(20, 432)
        self.chk_isolate.Size = Size(460, 24)
        self.chk_isolate.Checked = True
        self.Controls.Add(self.chk_isolate)

        self.btn_next = Button()
        self.btn_next.Text = "Suivant : Nommer les nouveaux types >"
        self.btn_next.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.btn_next.Size = Size(460, 40)
        self.btn_next.Location = Point(20, 465)
        style_primary(self.btn_next)
        self.btn_next.Click += self.OnNext
        self.Controls.Add(self.btn_next)

        btn_cancel = Button()
        btn_cancel.Text = "Annuler"
        btn_cancel.Font = Font("Segoe UI", 9)
        btn_cancel.Size = Size(150, 30)
        btn_cancel.Location = Point(185, 515)
        style_secondary(btn_cancel)
        btn_cancel.Click += self.OnCancel
        self.Controls.Add(btn_cancel)

    def OnSelectAll(self, sender, event):
        all_checked = all(self.chk_list.GetItemChecked(i) for i in range(self.chk_list.Items.Count))
        for i in range(self.chk_list.Items.Count):
            self.chk_list.SetItemChecked(i, not all_checked)

    def OnNext(self, sender, event):
        checked_indices = list(self.chk_list.CheckedIndices)
        if not checked_indices:
            MessageBox.Show("Veuillez selectionner au moins un type.", "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        self.selected_symbols = [self.groups[self.ordered_keys[i]]["symbol"] for i in checked_indices]
        self.isolate_view = self.chk_isolate.Checked
        self.DialogResult = DialogResult.OK
        self.Close()

    def OnCancel(self, sender, event):
        self.DialogResult = DialogResult.Cancel
        self.Close()


# ============================================================
# ETAPE 2 : NOMMAGE (PREFIXE/SUFFIXE) ET CREATION DES TYPES
# ============================================================
class NameTypesForm(Form):
    def __init__(self, selected_symbols):
        Form.__init__(self)
        self.selected_symbols = selected_symbols
        self.type_mapping = {}      # {int old_symbol_id: new_symbol}
        self.mapping_pairs = []     # [(old_symbol, new_symbol), ...] - pour l'affichage a l'etape 3
        self.created_types = []
        self.types_created = False
        self.proceed_to_replace = False
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "05 - Gestion des Types Miroir (Etape 2/3 - Nommage) - 🎩 by Manseur Mohamed"
        # 600 (et non 585) : les boutons du bas sont a y=505 sur 40px de haut
        # (bas a 545) alors que 585 de hauteur EXTERIEURE ne laisse qu'environ
        # 548px de zone client - 3px de marge, donc un risque de rognage au
        # moindre facteur d'echelle DPI. 600 => client ~563px.
        self.Size = Size(FORM_WIDTH, 600)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        add_header(self, "Etape 2/3 - Nommage")

        lbl_subtitle = Label()
        lbl_subtitle.Text = str(len(self.selected_symbols)) + " type(s) selectionne(s) a l'etape precedente"
        lbl_subtitle.Font = Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = Point(20, 50)
        lbl_subtitle.AutoSize = True
        self.Controls.Add(lbl_subtitle)

        lbl_prefix = Label()
        lbl_prefix.Text = "Prefixe :"
        lbl_prefix.Font = Font("Segoe UI", 9)
        lbl_prefix.Location = Point(20, 85)
        lbl_prefix.Size = Size(110, 25)
        self.Controls.Add(lbl_prefix)

        self.txt_prefix = TextBox()
        self.txt_prefix.Font = Font("Segoe UI", 9)
        self.txt_prefix.Text = ""
        self.txt_prefix.Location = Point(140, 82)
        self.txt_prefix.Size = Size(340, 25)
        self.txt_prefix.TextChanged += self.UpdatePreview
        self.Controls.Add(self.txt_prefix)

        lbl_suffix = Label()
        lbl_suffix.Text = "Suffixe :"
        lbl_suffix.Font = Font("Segoe UI", 9)
        lbl_suffix.Location = Point(20, 120)
        lbl_suffix.Size = Size(110, 25)
        self.Controls.Add(lbl_suffix)

        self.txt_suffix = TextBox()
        self.txt_suffix.Font = Font("Segoe UI", 9)
        self.txt_suffix.Text = "_Miroir"
        self.txt_suffix.Location = Point(140, 117)
        self.txt_suffix.Size = Size(340, 25)
        self.txt_suffix.TextChanged += self.UpdatePreview
        self.Controls.Add(self.txt_suffix)

        lbl_preview = Label()
        lbl_preview.Text = "Apercu des noms (ancien -> nouveau) :"
        lbl_preview.Font = Font("Segoe UI", 9)
        lbl_preview.Location = Point(20, 155)
        lbl_preview.Size = Size(460, 20)
        self.Controls.Add(lbl_preview)

        self.list_preview = ListBox()
        self.list_preview.Font = Font("Consolas", 8)
        self.list_preview.Location = Point(20, 178)
        self.list_preview.Size = Size(460, 180)
        self.Controls.Add(self.list_preview)

        lbl_log = Label()
        lbl_log.Text = "Journal :"
        lbl_log.Font = Font("Segoe UI", 9)
        lbl_log.Location = Point(20, 365)
        lbl_log.Size = Size(460, 20)
        self.Controls.Add(lbl_log)

        self.txt_log = TextBox()
        self.txt_log.Font = Font("Consolas", 8)
        self.txt_log.Multiline = True
        self.txt_log.ReadOnly = True
        self.txt_log.ScrollBars = ScrollBars.Vertical
        self.txt_log.Location = Point(20, 385)
        self.txt_log.Size = Size(460, 110)
        self.Controls.Add(self.txt_log)

        self.btn_action = Button()
        self.btn_action.Text = "Creer les types"
        self.btn_action.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.btn_action.Size = Size(300, 40)
        self.btn_action.Location = Point(20, 505)
        style_success(self.btn_action)
        self.btn_action.Click += self.OnAction
        self.Controls.Add(self.btn_action)

        self.btn_secondary = Button()
        self.btn_secondary.Text = "Annuler"
        self.btn_secondary.Font = Font("Segoe UI", 9)
        self.btn_secondary.Size = Size(150, 40)
        self.btn_secondary.Location = Point(330, 505)
        style_secondary(self.btn_secondary)
        self.btn_secondary.Click += self.OnSecondary
        self.Controls.Add(self.btn_secondary)

        self.UpdatePreview(None, None)

    def log(self, message):
        self.txt_log.AppendText(message + "\r\n")

    def UpdatePreview(self, sender, event):
        self.list_preview.Items.Clear()
        prefix = self.txt_prefix.Text
        suffix = self.txt_suffix.Text
        for sym in self.selected_symbols:
            old_name = safe_type_name(sym)
            new_name = prefix + old_name + suffix
            self.list_preview.Items.Add(old_name + "  ->  " + new_name)

    def OnAction(self, sender, event):
        if self.types_created:
            # Types deja crees (premier clic) : ce meme bouton, redevenu
            # "Suivant >", nous fait maintenant passer a l'etape 3.
            self.proceed_to_replace = True
            self.Close()
            return

        prefix = self.txt_prefix.Text.strip()
        suffix = self.txt_suffix.Text.strip()
        if not prefix and not suffix:
            MessageBox.Show(
                "Renseignez au moins un prefixe ou un suffixe (sinon le nouveau type porterait exactement le meme nom que l'original).",
                "Attention", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        self.txt_log.Clear()
        self.log("--- Creation des types ---")

        t = RevitTransaction(doc, "Creer types miroir")
        try:
            t.Start()
            for sym in self.selected_symbols:
                new_name = prefix + safe_type_name(sym) + suffix
                new_sym = find_or_create_type(sym, new_name, self.log)
                if new_sym is not None:
                    self.created_types.append(new_sym)
                    self.type_mapping[int(id_value(sym.Id))] = new_sym
                    self.mapping_pairs.append((sym, new_sym))
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self.log("ERREUR GLOBALE : " + str(ex))
            return

        self.log("--- " + str(len(self.created_types)) + " type(s) cree(s)/recupere(s) ---")

        if not self.created_types:
            MessageBox.Show("Aucun type n'a pu etre cree.", "Erreur", MessageBoxButtons.OK, MessageBoxIcon.Error)
            return

        self.types_created = True
        self.btn_action.Text = "Suivant : Remplacer les elements >"
        self.btn_secondary.Text = "Terminer sans remplacer"
        self.txt_prefix.Enabled = False
        self.txt_suffix.Enabled = False

    def OnSecondary(self, sender, event):
        # Avant creation : "Annuler" - rien n'a ete modifie, on ferme sans
        # rien renvoyer. Apres creation : "Terminer sans remplacer" - les
        # types crees restent (deja commit), on ferme juste sans passer a
        # l'etape 3.
        self.proceed_to_replace = False
        self.Close()


# ============================================================
# ETAPE 3 : REMPLACEMENT DES ELEMENTS
# ============================================================
class ReplaceForm(Form):
    def __init__(self, type_mapping, mapping_pairs):
        Form.__init__(self)
        self.type_mapping = type_mapping
        self.mapping_pairs = mapping_pairs
        self.replaced_count = 0
        self.skipped_count = 0
        self.done = False
        self.InitializeComponent()

    def InitializeComponent(self):
        self.Text = "05 - Gestion des Types Miroir (Etape 3/3 - Remplacement) - 🎩 by Manseur Mohamed"
        self.Size = Size(FORM_WIDTH, 490)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        add_header(self, "Etape 3/3 - Remplacement")

        lbl_subtitle = Label()
        lbl_subtitle.Text = str(len(self.mapping_pairs)) + " type(s) pret(s) a etre appliques a leurs elements miroir"
        lbl_subtitle.Font = Font("Segoe UI", 9)
        lbl_subtitle.ForeColor = Color.FromArgb(100, 100, 100)
        lbl_subtitle.Location = Point(20, 50)
        lbl_subtitle.AutoSize = True
        self.Controls.Add(lbl_subtitle)

        self.list_summary = ListBox()
        self.list_summary.Font = Font("Consolas", 8)
        self.list_summary.Location = Point(20, 80)
        self.list_summary.Size = Size(460, 150)
        for old_sym, new_sym in self.mapping_pairs:
            self.list_summary.Items.Add(safe_type_name(old_sym) + "  ->  " + safe_type_name(new_sym))
        self.Controls.Add(self.list_summary)

        lbl_log = Label()
        lbl_log.Text = "Journal :"
        lbl_log.Font = Font("Segoe UI", 9)
        lbl_log.Location = Point(20, 240)
        lbl_log.Size = Size(460, 20)
        self.Controls.Add(lbl_log)

        self.txt_log = TextBox()
        self.txt_log.Font = Font("Consolas", 8)
        self.txt_log.Multiline = True
        self.txt_log.ReadOnly = True
        self.txt_log.ScrollBars = ScrollBars.Vertical
        self.txt_log.Location = Point(20, 260)
        self.txt_log.Size = Size(460, 120)
        self.Controls.Add(self.txt_log)

        self.btn_replace = Button()
        self.btn_replace.Text = "Remplacer les elements"
        self.btn_replace.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.btn_replace.Size = Size(300, 40)
        self.btn_replace.Location = Point(20, 395)
        style_warning_action(self.btn_replace)
        self.btn_replace.Click += self.OnReplace
        self.Controls.Add(self.btn_replace)

        btn_close = Button()
        btn_close.Text = "Fermer"
        btn_close.Font = Font("Segoe UI", 9)
        btn_close.Size = Size(150, 40)
        btn_close.Location = Point(330, 395)
        style_secondary(btn_close)
        btn_close.Click += self.OnClose
        self.Controls.Add(btn_close)

    def log(self, message):
        self.txt_log.AppendText(message + "\r\n")

    def OnReplace(self, sender, event):
        if self.done:
            self.Close()
            return

        self.txt_log.Clear()
        self.log("--- Debut du remplacement des elements miroir ---")

        t = RevitTransaction(doc, "Remplacer elements miroir")
        try:
            t.Start()
            self.replaced_count, self.skipped_count = replace_elements(self.type_mapping, self.log)
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self.log("ERREUR GLOBALE : " + str(ex))
            return

        self.log("--- Remplacement termine : " + str(self.replaced_count) +
                  " remplace(s), " + str(self.skipped_count) + " ignore(s) ---")
        self.done = True
        self.btn_replace.Text = "Termine - Fermer"

    def OnClose(self, sender, event):
        self.Close()


# ============================================================
# FONCTION PRINCIPALE
# ============================================================
def main():
    mirrored_instances = get_mirrored_instances()
    if not mirrored_instances:
        MessageBox.Show("Aucun element miroir trouve dans ce document.", "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
        return {"success": False, "message": "Aucun element miroir trouve dans ce document."}

    groups = group_by_symbol(mirrored_instances)

    # --- Etape 1 : selection des types ---
    form1 = SelectTypesForm(groups)
    if form1.ShowDialog() != DialogResult.OK:
        return {"success": False, "message": "Operation annulee (etape 1 - selection)."}

    if form1.isolate_view:
        selected_sym_ids = set(int(id_value(s.Id)) for s in form1.selected_symbols)
        ids_to_isolate = [e.Id for e in mirrored_instances if int(id_value(e.Symbol.Id)) in selected_sym_ids]
        if ids_to_isolate:
            t_iso = RevitTransaction(doc, "Isoler elements miroir selectionnes")
            try:
                t_iso.Start()
                try:
                    # Reinitialiser un eventuel isolement deja actif : Revit
                    # refuse un second IsolateElementsTemporary tant que le
                    # precedent n'a pas ete leve. ResetTemporaryHideIsolate()
                    # leve une exception si la vue n'a AUCUN isolement actif
                    # (ce n'est pas un no-op), d'ou son propre try/except.
                    uidoc.ActiveView.ResetTemporaryHideIsolate()
                except Exception:
                    pass
                uidoc.ActiveView.IsolateElementsTemporary(List[ElementId](ids_to_isolate))
                t_iso.Commit()
            except Exception:
                if t_iso.HasStarted() and not t_iso.HasEnded():
                    t_iso.RollBack()

    # --- Etape 2 : nommage et creation des types ---
    # Le resultat exploite est l'ETAT DU FORMULAIRE (type_mapping rempli ou
    # non), pas la valeur de retour de ShowDialog() : fermer la fenetre
    # avec la croix APRES avoir deja cree des types renverrait
    # DialogResult.Cancel alors que les types existent bel et bien deja
    # dans la maquette (transaction commit) - se fier uniquement au
    # DialogResult rapporterait a tort une "annulation" a l'utilisateur.
    form2 = NameTypesForm(form1.selected_symbols)
    form2.ShowDialog()

    if not form2.type_mapping:
        return {"success": False, "message": "Operation annulee ou aucun type cree (etape 2 - nommage)."}

    if not form2.proceed_to_replace:
        return {
            "success": True,
            "message": str(len(form2.created_types)) + " type(s) cree(s)/recupere(s). Remplacement non effectue (arret apres l'etape 2).",
            "types_crees": [safe_type_name(t) for t in form2.created_types]
        }

    # --- Etape 3 : remplacement des elements ---
    form3 = ReplaceForm(form2.type_mapping, form2.mapping_pairs)
    form3.ShowDialog()

    return {
        "success": True,
        "message": (
            str(len(form2.created_types)) + " type(s) cree(s)/recupere(s), " +
            str(form3.replaced_count) + " element(s) remplace(s), " +
            str(form3.skipped_count) + " ignore(s)."
        ),
        "types_crees": [safe_type_name(t) for t in form2.created_types],
        "elements_remplaces": form3.replaced_count,
        "elements_ignores": form3.skipped_count
    }


# ============================================================
# EXECUTION
# ============================================================
try:
    OUT = main()
except Exception as ex:
    OUT = {"success": False, "message": "Erreur inattendue : " + str(ex)}
