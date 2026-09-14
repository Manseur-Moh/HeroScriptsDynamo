# -*- coding: utf-8 -*-
"""
COPIER DES VUES ENTRE MAQUETTES (+ Nord / Coordonnées)
===============================================================================
Version améliorée avec interface moderne
Noeud Python Dynamo
🎩 by Manseur Mohamed
"""

import clr
import sys
import traceback

try:
    IS_IRONPYTHON = "ironpython" in sys.version.lower()
except Exception:
    IS_IRONPYTHON = True

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("RevitServices")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewFamilyType,
    ViewFamily,
    View,
    ViewPlan,
    ViewSection,
    View3D,
    Level,
    ElementId,
    Transform,
    CopyPasteOptions,
    ElementTransformUtils,
    XYZ,
    ViewType,
    IDuplicateTypeNamesHandler,
    DuplicateTypeAction,
    ProjectPosition,
    RevitLinkInstance,
    ModelPathUtils,
    OpenOptions,
    DetachFromCentralOption,
    BasicFileInfo,
    BoundingBoxXYZ,
    DetailLine,
    DetailCurve,
    TextNote,
    Dimension,
    FamilyInstance,
    FilledRegion,
    IndependentTag,
    SpatialElementTag,
    RevisionCloud,
    ImageInstance,
    StorageType,
    BuiltInParameter,
    FilterElement
)

from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

import System.Windows.Forms as WF
import System.Drawing as WD
from System.Collections.Generic import List

try:
    from System.IO import File, Path
except Exception:
    import os

    class File(object):
        @staticmethod
        def Exists(path):
            try:
                return os.path.isfile(path)
            except Exception:
                return False

    class Path(object):
        @staticmethod
        def GetFullPath(path):
            return os.path.abspath(path)

        @staticmethod
        def GetFileName(path):
            return os.path.basename(path)


doc = DocumentManager.Instance.CurrentDBDocument
app = DocumentManager.Instance.CurrentUIApplication.Application


def id_value(element_id):
    # ElementId.IntegerValue est obsolete depuis Revit 2024 et supprime dans
    # les versions recentes, remplace par ElementId.Value. On teste la
    # presence de la nouvelle propriete pour rester compatible 2024-2027.
    if hasattr(element_id, "Value"):
        return element_id.Value
    return element_id.IntegerValue


# =============================================================================
# THÈME UI
# =============================================================================

COLOR_BG = WD.Color.FromArgb(245, 247, 251)
COLOR_CARD = WD.Color.White
# Bleu d'accentuation ALIGNE sur le reste de la suite (scripts 01-07 :
# Color.FromArgb(0, 120, 215)). La mise en page en cartes, elle, reste
# volontairement plus riche que celle des autres scripts.
COLOR_ACCENT = WD.Color.FromArgb(0, 120, 215)
COLOR_ACCENT_DARK = WD.Color.FromArgb(0, 99, 177)
COLOR_ACCENT_SOFT = WD.Color.FromArgb(235, 243, 252)
COLOR_HEADER_SUBTEXT = WD.Color.FromArgb(219, 234, 254)
COLOR_TEXT_DARK = WD.Color.FromArgb(17, 24, 39)
COLOR_TEXT_MUTED = WD.Color.FromArgb(107, 114, 128)
COLOR_BORDER = WD.Color.FromArgb(226, 229, 235)
COLOR_SUCCESS = WD.Color.FromArgb(22, 163, 74)
COLOR_WARNING = WD.Color.FromArgb(217, 119, 6)
COLOR_ERROR = WD.Color.FromArgb(220, 38, 38)

FONT_TITLE = WD.Font("Segoe UI", 16, WD.FontStyle.Bold)
FONT_SUBTITLE = WD.Font("Segoe UI", 9)
FONT_CARD_TITLE = WD.Font("Segoe UI", 11, WD.FontStyle.Bold)
FONT_NORMAL = WD.Font("Segoe UI", 9.5)
FONT_SMALL = WD.Font("Segoe UI", 8.5)
FONT_BUTTON = WD.Font("Segoe UI", 9.5, WD.FontStyle.Bold)
FONT_LIST = WD.Font("Segoe UI", 9)
# Meme signature que les scripts 01-07 : Segoe UI 9pt ITALIQUE.
FONT_SIGNATURE = WD.Font("Segoe UI", 9, WD.FontStyle.Italic)


# =============================================================================
# CONFIGURATION MÉTIER
# =============================================================================

FILTER_ALL = "Toutes les vues"

VIEW_TYPE_LABELS = {
    ViewType.FloorPlan: "Plan d'étage",
    ViewType.CeilingPlan: "Plan de plafond",
    ViewType.AreaPlan: "Plan de surface",
    ViewType.EngineeringPlan: "Plan structurel",
    ViewType.Section: "Coupe",
    ViewType.Elevation: "Élévation",
    ViewType.ThreeD: "Vue 3D",
    ViewType.DraftingView: "Vue de dessin",
    ViewType.Legend: "Légende",
    ViewType.Schedule: "Nomenclature"
}

VIEW_GROUPS = {
    "Plans": (
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.AreaPlan,
        ViewType.EngineeringPlan
    ),
    "Coupes": (ViewType.Section,),
    "Élévations": (ViewType.Elevation,),
    "Vues 3D": (ViewType.ThreeD,),
    "Vues de dessin": (ViewType.DraftingView,),
    "Légendes": (ViewType.Legend,),
    "Nomenclatures": (ViewType.Schedule,)
}

VIEW_GROUP_ORDER = [
    "Plans",
    "Coupes",
    "Élévations",
    "Vues 3D",
    "Vues de dessin",
    "Légendes",
    "Nomenclatures"
]

DIRECT_COPY_TYPES = set([
    ViewType.DraftingView,
    ViewType.Legend,
    ViewType.Schedule
])

VIEW_TYPE_TO_FAMILY = {
    ViewType.FloorPlan: ViewFamily.FloorPlan,
    ViewType.CeilingPlan: ViewFamily.CeilingPlan,
    ViewType.EngineeringPlan: ViewFamily.StructuralPlan
}

ANNOTATION_CLASSES_NO_DIM = (
    DetailCurve,
    DetailLine,
    TextNote,
    FamilyInstance,
    FilledRegion,
    IndependentTag,       # tags d'elements (portes, fenetres, generiques...)
    SpatialElementTag,    # tags de pieces / surfaces (RoomTag, AreaTag)
    RevisionCloud,
    ImageInstance          # images / rasters places dans la vue
)

# NB : les groupes de details (Autodesk.Revit.DB.Group, ViewSpecific) sont
# volontairement EXCLUS de cette liste. Copier le Group suffit a lui seul a
# ramener tous ses membres (comportement natif de CopyElements) ; si on
# ajoutait aussi la classe Group ici, chaque element membre continuerait
# EN PLUS d'etre remonte individuellement par le FilteredElementCollector
# (il reste un DetailCurve/TextNote/... a part entiere) et serait copie une
# seconde fois en double, hors du groupe.

# Parametres de vue deja geres explicitement ailleurs (nom en particulier,
# rendu unique via get_unique_view_name) : a exclure de la copie generique
# des parametres pour eviter d'ecraser cette logique de correspondance
# dediee. Le gabarit de vue n'a pas besoin d'etre liste ici : c'est un
# parametre de type ElementId, deja exclu par la regle generale qui ignore
# ce type de stockage (voir copy_view_parameters).
VIEW_PARAM_BIPS_HANDLED_ELSEWHERE = set([
    BuiltInParameter.VIEW_NAME
])


# =============================================================================
# UI HELPERS
# =============================================================================

def style_primary_button(btn):
    try:
        btn.FlatStyle = WF.FlatStyle.Flat
        btn.FlatAppearance.BorderSize = 0
        btn.FlatAppearance.MouseOverBackColor = COLOR_ACCENT_DARK
        btn.FlatAppearance.MouseDownBackColor = COLOR_ACCENT_DARK
        btn.BackColor = COLOR_ACCENT
        btn.ForeColor = WD.Color.White
        btn.Font = FONT_BUTTON
        btn.Cursor = WF.Cursors.Hand
    except Exception:
        pass


def style_secondary_button(btn):
    try:
        btn.FlatStyle = WF.FlatStyle.Flat
        btn.FlatAppearance.BorderSize = 1
        btn.FlatAppearance.BorderColor = COLOR_BORDER
        btn.FlatAppearance.MouseOverBackColor = COLOR_ACCENT_SOFT
        btn.FlatAppearance.MouseDownBackColor = COLOR_ACCENT_SOFT
        btn.BackColor = COLOR_CARD
        # Bleu fonce (et non COLOR_ACCENT) : le nouvel accent aligne sur la
        # suite est un peu plus clair que l'ancien, et le texte devenait
        # limite en contraste sur le fond COLOR_ACCENT_SOFT du survol.
        btn.ForeColor = COLOR_ACCENT_DARK
        btn.Font = FONT_BUTTON
        btn.Cursor = WF.Cursors.Hand
    except Exception:
        pass


def style_combo(cmb):
    try:
        cmb.FlatStyle = WF.FlatStyle.Flat
        cmb.BackColor = WD.Color.White
        cmb.ForeColor = COLOR_TEXT_DARK
        cmb.Font = FONT_NORMAL
    except Exception:
        pass


def style_textbox(txt):
    try:
        txt.BorderStyle = WF.BorderStyle.FixedSingle
        txt.BackColor = WD.Color.White
        txt.ForeColor = COLOR_TEXT_DARK
        txt.Font = FONT_NORMAL
    except Exception:
        pass


def style_radio(rb):
    try:
        rb.Font = FONT_NORMAL
        rb.ForeColor = COLOR_TEXT_DARK
    except Exception:
        pass


def style_checkbox(cb):
    try:
        cb.Font = FONT_NORMAL
        cb.ForeColor = COLOR_TEXT_DARK
    except Exception:
        pass


def style_label_muted(lbl):
    try:
        lbl.Font = FONT_SMALL
        lbl.ForeColor = COLOR_TEXT_MUTED
    except Exception:
        pass


# =============================================================================
# HANDLER DUPLICATION DE TYPES
# =============================================================================

class DuplicateTypeNamesHandler(IDuplicateTypeNamesHandler):
    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes


# =============================================================================
# HELPERS REVIT
# =============================================================================

def get_element_id(obj):
    if obj is None:
        return None
    if isinstance(obj, ElementId):
        return obj
    eid = getattr(obj, "Id", None)
    if isinstance(eid, ElementId):
        return eid
    return None


def to_element_id_list(elements=None):
    id_list = List[ElementId]()

    if elements is None:
        return id_list

    single_id = get_element_id(elements)
    if single_id is not None:
        id_list.Add(single_id)
        return id_list

    try:
        for elem in elements:
            eid = get_element_id(elem)
            if eid is not None:
                id_list.Add(eid)
    except Exception:
        pass

    return id_list


def collection_count(collection):
    if collection is None:
        return 0
    try:
        return collection.Count
    except Exception:
        pass
    try:
        return len(collection)
    except Exception:
        return 0


def make_copy_options():
    opts = CopyPasteOptions()
    try:
        opts.SetDuplicateTypeNamesHandler(DuplicateTypeNamesHandler())
    except Exception:
        pass
    return opts


def are_same_path(path1, path2):
    if not path1 or not path2:
        return False
    try:
        return Path.GetFullPath(path1).lower() == Path.GetFullPath(path2).lower()
    except Exception:
        return str(path1).lower() == str(path2).lower()


def are_same_document(document1, document2):
    if document1 is None or document2 is None:
        return False

    try:
        if document1.PathName and document2.PathName:
            if are_same_path(document1.PathName, document2.PathName):
                return True
    except Exception:
        pass

    try:
        return document1.Title == document2.Title
    except Exception:
        return False


def is_current_document(document):
    try:
        if document.PathName and doc.PathName:
            if are_same_path(document.PathName, doc.PathName):
                return True
    except Exception:
        pass

    try:
        return document.Title == doc.Title
    except Exception:
        return False


def close_document_quiet(document):
    if document is None:
        return
    try:
        if not document.IsLinked:
            document.Close(False)
    except Exception:
        pass


def find_open_document_by_path(path):
    for d in app.Documents:
        try:
            if d.PathName and are_same_path(d.PathName, path):
                return d
        except Exception:
            continue
    return None


def open_background_document(path):
    if not path:
        raise Exception("Chemin vide.")

    try:
        if not File.Exists(path):
            raise Exception("Fichier introuvable : {}".format(path))
    except Exception:
        raise Exception("Fichier introuvable ou inaccessible : {}".format(path))

    existing = find_open_document_by_path(path)
    if existing is not None:
        return existing, False

    model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    open_options = OpenOptions()

    try:
        file_info = BasicFileInfo.Extract(path)
        if file_info is not None and file_info.IsWorkshared:
            open_options.DetachFromCentralOption = DetachFromCentralOption.DetachAndDiscardWorksets
    except Exception:
        pass

    opened_doc = app.OpenDocumentFile(model_path, open_options)
    if opened_doc is None:
        raise Exception("Impossible d'ouvrir le fichier Revit.")

    return opened_doc, True


def collect_open_documents():
    result = {}

    for d in app.Documents:
        try:
            if d.IsLinked or d.IsFamilyDocument:
                continue
            if is_current_document(d):
                continue

            key = d.Title
            base_key = key
            i = 1

            while key in result:
                try:
                    suffix = Path.GetFileName(d.PathName) if d.PathName else str(i)
                except Exception:
                    suffix = str(i)
                key = "{} ({})".format(base_key, suffix)
                i += 1

            result[key] = d
        except Exception:
            continue

    return result


def collect_link_documents():
    result = {}

    for link_instance in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        try:
            link_doc = link_instance.GetLinkDocument()
            if link_doc is None:
                continue

            transform = link_instance.GetTotalTransform()
            key = "{} (lien)".format(link_doc.Title)
            base_key = key
            i = 1

            while key in result:
                key = "{} ({})".format(base_key, i)
                i += 1

            result[key] = (link_doc, transform)
        except Exception:
            continue

    return result


def get_existing_view_names(document):
    names = set()
    for v in FilteredElementCollector(document).OfClass(View):
        try:
            names.add(v.Name)
        except Exception:
            pass
    return names


def get_unique_view_name(base_name, used_names):
    base = base_name if base_name else "Vue"
    name = base
    counter = 1

    while name in used_names:
        name = "{} ({})".format(base, counter)
        counter += 1

    return name


def build_level_map(document):
    levels = {}
    for lvl in FilteredElementCollector(document).OfClass(Level):
        try:
            levels[lvl.Name] = lvl
        except Exception:
            pass
    return levels


def get_view_family_type(document, view_family, source_view=None):
    fallback = None
    source_type_name = None

    if source_view is not None:
        try:
            source_type = source_view.Document.GetElement(source_view.GetTypeId())
            if source_type is not None:
                source_type_name = source_type.Name
        except Exception:
            source_type_name = None

    for vft in FilteredElementCollector(document).OfClass(ViewFamilyType):
        try:
            if vft.ViewFamily == view_family:
                if source_type_name and vft.Name == source_type_name:
                    return vft
                if fallback is None:
                    fallback = vft
        except Exception:
            continue

    return fallback


def transform_bounding_box(box, transform):
    if box is None or transform is None:
        return box

    try:
        if transform.IsIdentity:
            return box
    except Exception:
        pass

    try:
        new_box = BoundingBoxXYZ()
        new_box.Transform = transform.Multiply(box.Transform)
        new_box.Min = box.Min
        new_box.Max = box.Max
        return new_box
    except Exception:
        return box


def safe_set(obj, prop, value):
    try:
        setattr(obj, prop, value)
        return True
    except Exception:
        return False


# =============================================================================
# TRANSFERT DE PROPRIÉTÉS / GABARITS
# =============================================================================

def copy_category_overrides(src_view, dest_view):
    """Copie les surcharges Visibilite/Graphisme (couleurs de traits,
    remplissages, transparence...) et la visibilite par categorie, de la
    vue source vers la vue cible.

    Limite aux categories INTEGREES de Revit (Id negatif) : leur ElementId
    est garanti identique dans tous les documents, contrairement aux
    categories de projet personnalisees (Id positif) qui ne le sont pas -
    celles-ci sont ignorees par securite plutot que de risquer d'appliquer
    une surcharge a la mauvaise categorie dans le document cible."""
    try:
        categories = src_view.Document.Settings.Categories
    except Exception:
        return

    for cat in categories:
        try:
            cat_id = cat.Id
            if id_value(cat_id) >= 0:
                continue

            try:
                if src_view.GetCategoryHidden(cat_id):
                    dest_view.SetCategoryHidden(cat_id, True)
            except Exception:
                pass

            try:
                overrides = src_view.GetCategoryOverrides(cat_id)
                dest_view.SetCategoryOverrides(cat_id, overrides)
            except Exception:
                pass
        except Exception:
            continue


def apply_graphic_settings(src_view, dest_view, src_transform=None, include_crop=True, dest_doc=None, filter_cache=None):
    for prop in ["Scale", "DetailLevel", "Discipline", "DisplayStyle", "PartsVisibility"]:
        try:
            safe_set(dest_view, prop, getattr(src_view, prop))
        except Exception:
            pass

    for prop in ["CropBoxActive", "CropBoxVisible"]:
        try:
            safe_set(dest_view, prop, getattr(src_view, prop))
        except Exception:
            pass

    if include_crop:
        try:
            crop = src_view.CropBox
            if crop is not None and src_transform is not None:
                crop = transform_bounding_box(crop, src_transform)
            if crop is not None:
                dest_view.CropBox = crop
        except Exception:
            pass

    copy_category_overrides(src_view, dest_view)

    # Filtres de vue (regles de surcharge Visibilite/Graphisme par filtre) :
    # necessite le document cible pour dupliquer les FilterElement absents,
    # d'ou les parametres optionnels dest_doc/filter_cache - non fournis
    # depuis d'anciens appels, la copie des filtres est alors simplement
    # ignoree plutot que de lever une erreur.
    if dest_doc is not None and filter_cache is not None:
        copy_view_filters(src_view, dest_view, dest_doc, filter_cache)


def ensure_view_filter(src_doc, dest_doc, filter_id, cache):
    """Retrouve (par nom) ou duplique dans dest_doc le FilterElement
    (ParameterFilterElement / SelectionFilterElement) identifie par
    filter_id dans src_doc, et renvoie son Id dans dest_doc.

    Meme logique que ensure_view_template : recherche par nom d'abord (le
    filtre existe peut-etre deja dans la cible), sinon copie de l'element
    via ElementTransformUtils.CopyElements."""
    if filter_id is None:
        return None

    try:
        if filter_id == ElementId.InvalidElementId:
            return None
        key = int(id_value(filter_id))
    except Exception:
        return None

    if key in cache:
        return cache[key]

    src_filter = src_doc.GetElement(filter_id)
    if src_filter is None:
        cache[key] = None
        return None

    filter_name = None
    try:
        filter_name = src_filter.Name
    except Exception:
        pass

    if filter_name:
        for f in FilteredElementCollector(dest_doc).OfClass(FilterElement):
            try:
                if f.Name == filter_name:
                    cache[key] = f.Id
                    return f.Id
            except Exception:
                continue

    try:
        opts = make_copy_options()
        new_ids = ElementTransformUtils.CopyElements(
            src_doc,
            to_element_id_list([filter_id]),
            dest_doc,
            Transform.Identity,
            opts
        )

        if new_ids is not None:
            for new_id in new_ids:
                element = dest_doc.GetElement(new_id)
                if isinstance(element, FilterElement):
                    cache[key] = element.Id
                    return element.Id
    except Exception:
        pass

    if filter_name:
        for f in FilteredElementCollector(dest_doc).OfClass(FilterElement):
            try:
                if f.Name == filter_name:
                    cache[key] = f.Id
                    return f.Id
            except Exception:
                continue

    cache[key] = None
    return None


def copy_view_filters(src_view, dest_view, dest_doc, filter_cache):
    """Applique a dest_view les memes filtres de vue que src_view, avec
    leurs surcharges graphiques et leur etat active/visible - en dupliquant
    au besoin le FilterElement dans le document cible.

    Chaque etape est individuellement protegee : un filtre qui pose
    probleme (nom en conflit, methode absente sur une version de Revit
    plus ancienne...) est ignore sans interrompre les suivants."""
    try:
        filter_ids = list(src_view.GetFilters())
    except Exception:
        return 0

    applied = 0

    for filter_id in filter_ids:
        try:
            dest_filter_id = ensure_view_filter(src_view.Document, dest_doc, filter_id, filter_cache)
            if dest_filter_id is None:
                continue

            try:
                existing = list(dest_view.GetFilters())
            except Exception:
                existing = []

            if dest_filter_id not in existing:
                try:
                    dest_view.AddFilter(dest_filter_id)
                except Exception:
                    continue

            try:
                overrides = src_view.GetFilterOverrides(filter_id)
                dest_view.SetFilterOverrides(dest_filter_id, overrides)
            except Exception:
                pass

            # Nom de methode different selon les versions de Revit pour
            # l'etat "actif" (case a cocher) du filtre - on tente les deux.
            try:
                enabled = src_view.GetIsFilterEnabled(filter_id)
                dest_view.SetIsFilterEnabled(dest_filter_id, enabled)
            except Exception:
                pass

            try:
                visible = src_view.GetFilterVisibility(filter_id)
                dest_view.SetFilterVisibility(dest_filter_id, visible)
            except Exception:
                pass

            applied += 1
        except Exception:
            continue

    return applied


def get_builtin_parameter(param):
    """Renvoie le BuiltInParameter d'un Parameter s'il en a un
    (BuiltInParameter.INVALID sinon, typiquement pour un parametre de
    projet/partage) - jamais d'exception meme sur une Definition atypique."""
    try:
        bip = param.Definition.BuiltInParameter
        if bip is not None and bip != BuiltInParameter.INVALID:
            return bip
    except Exception:
        pass
    return None


def copy_view_parameters(src_view, dest_view, dest_doc):
    """Copie les parametres (integres et de projet) de la vue source vers
    la vue cible - tout ce qui n'est pas deja gere par une logique dediee
    ailleurs (nom rendu unique, gabarit, echelle/niveau de detail/
    discipline/style d'affichage/cadrage deja geres par
    apply_graphic_settings).

    Se limite aux types de stockage texte/entier/reel : un parametre de
    type ElementId (sous-categorie de ligne, filtre de phase...) reference
    un element du document SOURCE et n'a aucune raison de designer le bon
    element dans le document cible - le copier tel quel corromprait
    silencieusement la vue plutot que d'echouer. Seule la Phase beneficie
    d'une correspondance explicite par NOM (meme principe que le script 03),
    car c'est le parametre ElementId le plus frequemment attendu."""
    applied = 0

    try:
        params = list(src_view.Parameters)
    except Exception:
        return 0

    for p in params:
        try:
            if p is None or not p.HasValue or p.IsReadOnly:
                continue

            definition = p.Definition
            if definition is None:
                continue

            bip = get_builtin_parameter(p)

            if bip is not None and bip in VIEW_PARAM_BIPS_HANDLED_ELSEWHERE:
                continue

            if bip == getattr(BuiltInParameter, "VIEW_PHASE", None):
                try:
                    src_phase = src_view.Document.GetElement(p.AsElementId())
                    if src_phase is not None:
                        phase_name = src_phase.Name
                        for ph in dest_doc.Phases:
                            if ph.Name == phase_name:
                                dest_param = dest_view.get_Parameter(bip)
                                if dest_param is not None and not dest_param.IsReadOnly:
                                    dest_param.Set(ph.Id)
                                    applied += 1
                                break
                except Exception:
                    pass
                continue

            if p.StorageType == StorageType.ElementId:
                continue

            dest_param = None
            try:
                if bip is not None:
                    dest_param = dest_view.get_Parameter(bip)
                elif definition.Name:
                    dest_param = dest_view.LookupParameter(definition.Name)
            except Exception:
                dest_param = None

            if dest_param is None or dest_param.IsReadOnly:
                continue

            if dest_param.StorageType != p.StorageType:
                continue

            if p.StorageType == StorageType.String:
                dest_param.Set(p.AsString())
            elif p.StorageType == StorageType.Integer:
                dest_param.Set(p.AsInteger())
            elif p.StorageType == StorageType.Double:
                dest_param.Set(p.AsDouble())
            else:
                continue

            applied += 1
        except Exception:
            continue

    return applied


def ensure_view_template(src_doc, dest_doc, template_id, cache):
    if template_id is None:
        return None

    try:
        if template_id == ElementId.InvalidElementId:
            return None
        key = int(id_value(template_id))
    except Exception:
        return None

    if key in cache:
        return cache[key]

    src_template = src_doc.GetElement(template_id)
    if src_template is None:
        cache[key] = None
        return None

    template_name = None
    try:
        template_name = src_template.Name
    except Exception:
        pass

    if template_name:
        for v in FilteredElementCollector(dest_doc).OfClass(View):
            try:
                if v.IsTemplate and v.Name == template_name:
                    cache[key] = v.Id
                    return v.Id
            except Exception:
                continue

    try:
        opts = make_copy_options()
        new_ids = ElementTransformUtils.CopyElements(
            src_doc,
            to_element_id_list([template_id]),
            dest_doc,
            Transform.Identity,
            opts
        )

        if new_ids is not None:
            for new_id in new_ids:
                element = dest_doc.GetElement(new_id)
                if isinstance(element, View) and element.IsTemplate:
                    cache[key] = element.Id
                    return element.Id
    except Exception:
        pass

    if template_name:
        for v in FilteredElementCollector(dest_doc).OfClass(View):
            try:
                if v.IsTemplate and v.Name == template_name:
                    cache[key] = v.Id
                    return v.Id
            except Exception:
                continue

    cache[key] = None
    return None


def apply_view_properties(
    src_view,
    dest_view,
    dest_doc,
    template_cache,
    view_names,
    include_graphics,
    include_template,
    src_transform=None,
    filter_cache=None
):
    if include_graphics:
        include_crop = True
        try:
            if isinstance(src_view, ViewSection) or isinstance(src_view, View3D):
                include_crop = False
        except Exception:
            pass

        apply_graphic_settings(
            src_view, dest_view, src_transform, include_crop,
            dest_doc=dest_doc, filter_cache=filter_cache
        )

    try:
        new_name = get_unique_view_name(src_view.Name, view_names)
        if dest_view.Name != new_name:
            dest_view.Name = new_name
        view_names.add(new_name)
    except Exception:
        pass

    if include_template:
        try:
            template_id = src_view.ViewTemplateId
            if template_id is not None and template_id != ElementId.InvalidElementId:
                dest_template_id = ensure_view_template(
                    src_view.Document,
                    dest_doc,
                    template_id,
                    template_cache
                )
                if dest_template_id is not None:
                    dest_view.ViewTemplateId = dest_template_id
        except Exception:
            pass

    # Parametres de la vue (integres et de projet) : copiee inconditionnel-
    # lement (pas seulement si include_graphics), car il s'agit de donnees
    # de vue au sens large et pas uniquement de reglages graphiques.
    try:
        copy_view_parameters(src_view, dest_view, dest_doc)
    except Exception:
        pass


# =============================================================================
# COPIE DES ANNOTATIONS / DÉTAILS 2D
# =============================================================================

def collect_view_annotation_ids(src_doc, src_view):
    all_ids = []
    dimension_ints = set()

    try:
        collector = FilteredElementCollector(src_doc, src_view.Id)
        collector.WhereElementIsNotElementType()

        for elem in collector:
            try:
                if elem.Id == src_view.Id:
                    continue

                if not elem.ViewSpecific:
                    continue

                if isinstance(elem, Dimension):
                    all_ids.append(elem.Id)
                    dimension_ints.add(int(id_value(elem.Id)))
                elif isinstance(elem, ANNOTATION_CLASSES_NO_DIM):
                    all_ids.append(elem.Id)
            except Exception:
                continue
    except Exception:
        pass

    return all_ids, dimension_ints


def copy_view_specific_elements(src_view, dest_view):
    try:
        all_ids, dimension_ints = collect_view_annotation_ids(src_view.Document, src_view)

        if not all_ids:
            return 0, None

        opts = make_copy_options()

        try:
            new_ids = ElementTransformUtils.CopyElements(
                src_view,
                to_element_id_list(all_ids),
                dest_view,
                opts
            )
            return collection_count(new_ids), None
        except Exception as ex:
            non_dimension_ids = [
                eid for eid in all_ids
                if int(id_value(eid)) not in dimension_ints
            ]

            if non_dimension_ids:
                try:
                    new_ids = ElementTransformUtils.CopyElements(
                        src_view,
                        to_element_id_list(non_dimension_ids),
                        dest_view,
                        opts
                    )
                    return collection_count(new_ids), "Dimensions non copiées : {}".format(ex)
                except Exception as ex2:
                    return 0, str(ex2)

            return 0, str(ex)

    except Exception as ex:
        return 0, str(ex)


# =============================================================================
# COPIE / RECRÉATION DES VUES
# =============================================================================

def copy_direct_view(
    src_view,
    dest_doc,
    template_cache,
    view_names,
    include_graphics,
    include_template,
    src_transform=None,
    filter_cache=None
):
    try:
        opts = make_copy_options()

        # Copier uniquement l'Id de la vue ne copie que son CONTENEUR : les
        # elements qu'elle heberge (lignes de detail, textes, regions
        # remplies, tags...) ne sont pas traverses automatiquement par
        # CopyElements et restaient donc absents de la vue copiee (vues de
        # dessin, legendes). En les incluant dans la MEME operation de
        # copie, Revit les rattache a la nouvelle vue - comme le fait un
        # copier/coller manuel d'une vue dans l'interface de Revit.
        ids_to_copy = [src_view.Id]
        try:
            annotation_ids, _ = collect_view_annotation_ids(src_view.Document, src_view)
            ids_to_copy.extend(annotation_ids)
        except Exception:
            pass

        new_ids = ElementTransformUtils.CopyElements(
            src_view.Document,
            to_element_id_list(ids_to_copy),
            dest_doc,
            Transform.Identity,
            opts
        )

        if new_ids is None:
            return None, "Aucun élément copié"

        for new_id in new_ids:
            new_view = dest_doc.GetElement(new_id)
            if isinstance(new_view, View):
                apply_view_properties(
                    src_view,
                    new_view,
                    dest_doc,
                    template_cache,
                    view_names,
                    include_graphics,
                    include_template,
                    src_transform,
                    filter_cache
                )
                return new_view, None

        return None, "La copie n'a pas produit de vue"
    except Exception as ex:
        return None, str(ex)


def recreate_plan_view(src_view, dest_doc, level_map):
    try:
        if src_view.ViewType == ViewType.AreaPlan:
            return None, "Les plans de surface ne sont pas pris en charge par ce script"

        try:
            gen_level = src_view.GenLevel
        except Exception:
            gen_level = None

        if gen_level is None:
            return None, "Niveau source introuvable"

        dest_level = level_map.get(gen_level.Name)
        if dest_level is None:
            return None, "Niveau '{}' absent du document cible".format(gen_level.Name)

        target_family = VIEW_TYPE_TO_FAMILY.get(src_view.ViewType)
        if target_family is None:
            return None, "Type de plan non pris en charge"

        vft = get_view_family_type(dest_doc, target_family, src_view)
        if vft is None:
            return None, "Type de famille de vue introuvable dans la cible"

        new_view = ViewPlan.Create(dest_doc, vft.Id, dest_level.Id)
        return new_view, None

    except Exception as ex:
        return None, str(ex)


def make_valid_section_box(box):
    """Renvoie une BoundingBoxXYZ garantie exploitable par
    ViewSection.CreateSection, qui exige des vecteurs de base UNITAIRES et
    STRICTEMENT ORTHOGONAUX, ainsi que des bornes Min/Max non inversees et
    suffisamment espacees sur les 3 axes.

    Reprendre directement view.CropBox (eventuellement apres
    transform_bounding_box, qui multiplie deux transforms) peut accumuler
    de petites derives d'arrondi qui suffisent a faire echouer cette
    validation stricte - Revit renvoie alors l'exception :
    "The BoundingBoxXYZ is not appropriate for detail views...".
    On re-orthonormalise la base (Gram-Schmidt) et on garantit un ecart
    minimal entre Min et Max par securite."""
    try:
        t = box.Transform

        basis_x = t.BasisX.Normalize()
        # Gram-Schmidt : rendre BasisY perpendiculaire a BasisX puis unitaire
        proj = basis_x.Multiply(basis_x.DotProduct(t.BasisY))
        basis_y = (t.BasisY - proj).Normalize()
        basis_z = basis_x.CrossProduct(basis_y).Normalize()

        new_transform = Transform.Identity
        new_transform.Origin = t.Origin
        new_transform.BasisX = basis_x
        new_transform.BasisY = basis_y
        new_transform.BasisZ = basis_z

        min_vals = [box.Min.X, box.Min.Y, box.Min.Z]
        max_vals = [box.Max.X, box.Max.Y, box.Max.Z]

        min_gap = 0.1  # pieds (~3 cm) : marge de securite minimale par axe
        for i in range(3):
            if max_vals[i] < min_vals[i]:
                min_vals[i], max_vals[i] = max_vals[i], min_vals[i]
            if (max_vals[i] - min_vals[i]) < min_gap:
                center = (max_vals[i] + min_vals[i]) / 2.0
                min_vals[i] = center - min_gap / 2.0
                max_vals[i] = center + min_gap / 2.0

        new_box = BoundingBoxXYZ()
        new_box.Transform = new_transform
        new_box.Min = XYZ(min_vals[0], min_vals[1], min_vals[2])
        new_box.Max = XYZ(max_vals[0], max_vals[1], max_vals[2])
        return new_box
    except Exception:
        return box


def recreate_section_view(src_view, dest_doc, src_transform):
    try:
        vft = get_view_family_type(dest_doc, ViewFamily.Section, src_view)
        if vft is None:
            return None, "Type de coupe introuvable dans la cible"

        crop_box = src_view.CropBox
        if crop_box is None:
            return None, "Boîte de coupe source introuvable"

        if src_transform is not None:
            crop_box = transform_bounding_box(crop_box, src_transform)

        crop_box = make_valid_section_box(crop_box)

        try:
            new_view = ViewSection.CreateSection(dest_doc, vft.Id, crop_box)
        except Exception as ex:
            return None, "Impossible de recreer cette coupe (geometrie de recadrage non exploitable) : {}".format(ex)

        return new_view, None

    except Exception as ex:
        return None, str(ex)


def recreate_3d_view(src_view, dest_doc, src_transform):
    try:
        vft = get_view_family_type(dest_doc, ViewFamily.ThreeDimensional, src_view)
        if vft is None:
            return None, "Type 3D introuvable dans la cible"

        new_view = View3D.CreateIsometric(dest_doc, vft.Id)

        try:
            orientation = src_view.GetOrientation()
            if orientation is not None:
                new_view.SetOrientation(orientation)
        except Exception:
            pass

        try:
            section_box = src_view.GetSectionBox()
            if section_box is not None:
                if src_transform is not None:
                    section_box = transform_bounding_box(section_box, src_transform)
                new_view.SetSectionBox(section_box)
        except Exception:
            pass

        return new_view, None

    except Exception as ex:
        return None, str(ex)


# =============================================================================
# COORDONNÉES / NORD
# =============================================================================

def transfer_project_position(src_doc, dest_doc, copy_north, copy_coords):
    if not copy_north and not copy_coords:
        return True, None

    try:
        src_position = src_doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
        dest_position = dest_doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)

        angle = src_position.Angle if copy_north else dest_position.Angle

        ew = src_position.EastWest if copy_coords else dest_position.EastWest
        ns = src_position.NorthSouth if copy_coords else dest_position.NorthSouth
        elevation = src_position.Elevation if copy_coords else dest_position.Elevation

        dest_doc.ActiveProjectLocation.SetProjectPosition(
            XYZ.Zero,
            ProjectPosition(ew, ns, elevation, angle)
        )

        return True, None

    except Exception as ex:
        return False, str(ex)


# =============================================================================
# RAPPORT
# =============================================================================

def build_report(
    source_title,
    target_title,
    selected_count,
    north_geo,
    coords_geo,
    copied_views,
    recreated_views,
    ignored_views,
    warnings,
    annotation_count,
    position_message
):
    lines = []

    lines.append("=" * 70)
    lines.append("RAPPORT DE COPIE DE VUES")
    lines.append("=" * 70)
    lines.append("Moteur Python : {}".format("IronPython 2" if IS_IRONPYTHON else "CPython 3"))
    lines.append("Source : {}".format(source_title))
    lines.append("Cible : {}".format(target_title))
    lines.append("Vues sélectionnées : {}".format(selected_count))
    lines.append("Nord : {}".format("Géographique (source)" if north_geo else "Projet (inchangé)"))
    lines.append("Coordonnées : {}".format("Partagées (source)" if coords_geo else "Internes (inchangé)"))

    if position_message:
        lines.append("Position : {}".format(position_message))

    lines.append("")
    lines.append("✓ Copiées directement : {}".format(len(copied_views)))
    lines.append("↻ Recréées : {}".format(len(recreated_views)))
    lines.append("✗ Ignorées : {}".format(len(ignored_views)))
    lines.append("Annotations copiées : {}".format(annotation_count))
    lines.append("")

    if copied_views:
        lines.append("VUES COPIÉES DIRECTEMENT :")
        for name in copied_views:
            lines.append("  ✓ {}".format(name))
        lines.append("")

    if recreated_views:
        lines.append("VUES RECRÉÉES :")
        for name in recreated_views:
            lines.append("  ↻ {}".format(name))
        lines.append("")

    if ignored_views:
        lines.append("VUES IGNORÉES :")
        for name, reason in ignored_views:
            lines.append("  ✗ {} : {}".format(name, reason))
        lines.append("")

    if warnings:
        lines.append("AVERTISSEMENTS :")
        for warning in warnings:
            lines.append("  ! {}".format(warning))

    lines.append("")
    lines.append("🎩 by Manseur Mohamed")

    return "\n".join(lines)


# =============================================================================
# INTERFACE UTILISATEUR MODERNE
# =============================================================================

class CopyViewsForm(WF.Form):
    def __init__(self, target_title):
        self.Text = "08 - Copier des vues entre maquettes - 🎩 by Manseur Mohamed"
        # Marge de securite (872 -> 900) : la barre d'action (Annuler /
        # Copier les vues) etait calee EXACTEMENT sur le bord bas du
        # ClientSize (808+64 = 872), ce qui pouvait, avec la mise a
        # l'echelle DPI de Windows, declencher une barre de defilement
        # parasite qui la masquait. On garde AutoScroll actif (pour les
        # ecrans trop petits ou 900px ne tiendraient pas a l'ecran, ce qui
        # rendrait sinon la carte 5 - Nord/coordonnees - inaccessible), la
        # marge suffit a eviter le declenchement parasite dans le cas normal.
        self.ClientSize = WD.Size(1024, 900)
        self.StartPosition = WF.FormStartPosition.CenterScreen
        self.FormBorderStyle = WF.FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.MinimizeBox = False
        self.BackColor = COLOR_BG
        self.AutoScroll = True
        self.TopMost = True

        # Données internes
        self.open_docs_map = {}
        self.link_docs_map = {}
        self.current_source_doc = None
        self.current_source_transform = None
        self.background_doc = None

        self.view_data = {}
        self.view_meta = []
        self.checked_ids = set()
        self._suppress_check_events = False

        # ---------------------------------------------------------------------
        # HEADER
        # ---------------------------------------------------------------------
        header = WF.Panel()
        header.SetBounds(0, 0, 1024, 80)
        header.BackColor = COLOR_ACCENT
        self.Controls.Add(header)

        lbl_title = WF.Label()
        lbl_title.Text = "Copier des vues entre maquettes"
        lbl_title.Font = FONT_TITLE
        lbl_title.ForeColor = WD.Color.White
        lbl_title.BackColor = COLOR_ACCENT
        lbl_title.SetBounds(24, 16, 700, 30)
        header.Controls.Add(lbl_title)

        lbl_sub = WF.Label()
        lbl_sub.Text = "Document cible : {}".format(target_title)
        lbl_sub.Font = FONT_SUBTITLE
        lbl_sub.ForeColor = COLOR_HEADER_SUBTEXT
        lbl_sub.BackColor = COLOR_ACCENT
        lbl_sub.SetBounds(24, 48, 850, 18)
        header.Controls.Add(lbl_sub)

        lbl_sig = WF.Label()
        lbl_sig.Text = "🎩 by Manseur Mohamed"
        lbl_sig.Font = FONT_SIGNATURE
        lbl_sig.ForeColor = COLOR_HEADER_SUBTEXT
        lbl_sig.BackColor = COLOR_ACCENT
        lbl_sig.TextAlign = WD.ContentAlignment.MiddleRight
        lbl_sig.SetBounds(724, 16, 276, 20)
        header.Controls.Add(lbl_sig)

        # ---------------------------------------------------------------------
        # CARD 1 - SOURCE
        # ---------------------------------------------------------------------
        card_source = self.add_card(20, 92, 984, 130)
        self.add_card_title(card_source, "1. Source")

        self.rb_open = WF.RadioButton()
        self.rb_open.Text = "Document ouvert"
        self.rb_open.Checked = True
        self.rb_open.SetBounds(16, 44, 150, 24)
        style_radio(self.rb_open)
        card_source.Controls.Add(self.rb_open)

        self.rb_link = WF.RadioButton()
        self.rb_link.Text = "Lien Revit"
        self.rb_link.SetBounds(176, 44, 140, 24)
        style_radio(self.rb_link)
        card_source.Controls.Add(self.rb_link)

        self.rb_file = WF.RadioButton()
        self.rb_file.Text = "Fichier .rvt"
        self.rb_file.SetBounds(326, 44, 140, 24)
        style_radio(self.rb_file)
        card_source.Controls.Add(self.rb_file)

        self.cbo_docs = WF.ComboBox()
        self.cbo_docs.DropDownStyle = WF.ComboBoxStyle.DropDownList
        self.cbo_docs.SetBounds(16, 82, 650, 30)
        style_combo(self.cbo_docs)
        card_source.Controls.Add(self.cbo_docs)

        self.txt_path = WF.TextBox()
        self.txt_path.SetBounds(16, 82, 520, 30)
        self.txt_path.Visible = False
        style_textbox(self.txt_path)
        card_source.Controls.Add(self.txt_path)

        self.btn_browse = WF.Button()
        self.btn_browse.Text = "Parcourir"
        self.btn_browse.SetBounds(548, 82, 118, 30)
        self.btn_browse.Visible = False
        style_secondary_button(self.btn_browse)
        card_source.Controls.Add(self.btn_browse)

        self.btn_load = WF.Button()
        self.btn_load.Text = "Charger les vues"
        self.btn_load.SetBounds(680, 82, 288, 32)
        style_primary_button(self.btn_load)
        card_source.Controls.Add(self.btn_load)
        self.update_load_button_state(False)

        # ---------------------------------------------------------------------
        # CARD 2 - FILTRES
        # ---------------------------------------------------------------------
        card_filter = self.add_card(20, 234, 984, 88)
        self.add_card_title(card_filter, "2. Filtres et sélection")

        lbl_search = WF.Label()
        lbl_search.Text = "Recherche"
        lbl_search.SetBounds(16, 52, 70, 18)
        style_label_muted(lbl_search)
        card_filter.Controls.Add(lbl_search)

        self.txt_search = WF.TextBox()
        self.txt_search.SetBounds(90, 48, 220, 28)
        style_textbox(self.txt_search)
        card_filter.Controls.Add(self.txt_search)

        lbl_type = WF.Label()
        lbl_type.Text = "Type"
        lbl_type.SetBounds(328, 52, 40, 18)
        style_label_muted(lbl_type)
        card_filter.Controls.Add(lbl_type)

        self.cbo_filter = WF.ComboBox()
        self.cbo_filter.DropDownStyle = WF.ComboBoxStyle.DropDownList
        self.cbo_filter.SetBounds(372, 48, 185, 28)
        style_combo(self.cbo_filter)
        self.cbo_filter.Items.Add(FILTER_ALL)
        for label in VIEW_GROUP_ORDER:
            self.cbo_filter.Items.Add(label)
        self.cbo_filter.SelectedIndex = 0
        card_filter.Controls.Add(self.cbo_filter)

        self.btn_select_all = WF.Button()
        self.btn_select_all.Text = "Tout cocher"
        self.btn_select_all.SetBounds(575, 47, 125, 30)
        style_secondary_button(self.btn_select_all)
        card_filter.Controls.Add(self.btn_select_all)

        self.btn_select_none = WF.Button()
        self.btn_select_none.Text = "Décocher"
        self.btn_select_none.SetBounds(708, 47, 125, 30)
        style_secondary_button(self.btn_select_none)
        card_filter.Controls.Add(self.btn_select_none)

        self.btn_invert = WF.Button()
        self.btn_invert.Text = "Inverser"
        self.btn_invert.SetBounds(841, 47, 127, 30)
        style_secondary_button(self.btn_invert)
        card_filter.Controls.Add(self.btn_invert)

        # ---------------------------------------------------------------------
        # CARD 3 - VUES
        # ---------------------------------------------------------------------
        card_views = self.add_card(20, 334, 984, 300)
        self.lbl_views_title = self.add_card_title(card_views, "3. Vues")

        self.lv_views = WF.ListView()
        self.lv_views.SetBounds(16, 44, 952, 240)
        self.lv_views.CheckBoxes = True
        self.lv_views.View = WF.View.Details
        self.lv_views.FullRowSelect = True
        self.lv_views.GridLines = False
        self.lv_views.HideSelection = False
        self.lv_views.BorderStyle = WF.BorderStyle.None
        self.lv_views.BackColor = WD.Color.White
        self.lv_views.ForeColor = COLOR_TEXT_DARK
        self.lv_views.Font = FONT_LIST

        self.lv_views.Columns.Add("Nom", 420)
        self.lv_views.Columns.Add("Type", 190)
        self.lv_views.Columns.Add("Échelle", 90)
        self.lv_views.Columns.Add("Niveau", 230)

        card_views.Controls.Add(self.lv_views)

        # ---------------------------------------------------------------------
        # CARD 4 - OPTIONS
        # ---------------------------------------------------------------------
        card_options = self.add_card(20, 646, 486, 150)
        self.add_card_title(card_options, "4. Options de copie")

        self.chk_copy_annotations = WF.CheckBox()
        self.chk_copy_annotations.Text = "Copier détails / annotations (vues recréées)"
        self.chk_copy_annotations.Checked = True
        self.chk_copy_annotations.SetBounds(16, 46, 454, 24)
        style_checkbox(self.chk_copy_annotations)
        card_options.Controls.Add(self.chk_copy_annotations)

        self.chk_copy_templates = WF.CheckBox()
        self.chk_copy_templates.Text = "Copier les gabarits de vue"
        self.chk_copy_templates.Checked = True
        self.chk_copy_templates.SetBounds(16, 76, 454, 24)
        style_checkbox(self.chk_copy_templates)
        card_options.Controls.Add(self.chk_copy_templates)

        self.chk_copy_graphics = WF.CheckBox()
        self.chk_copy_graphics.Text = "Copier les propriétés graphiques"
        self.chk_copy_graphics.Checked = True
        self.chk_copy_graphics.SetBounds(16, 106, 454, 24)
        style_checkbox(self.chk_copy_graphics)
        card_options.Controls.Add(self.chk_copy_graphics)

        # ---------------------------------------------------------------------
        # CARD 5 - NORD / COORDONNÉES
        # ---------------------------------------------------------------------
        card_coords = self.add_card(518, 646, 486, 150)
        self.add_card_title(card_coords, "5. Nord et coordonnées")

        # IMPORTANT : dans WinForms, les RadioButton sont mutuellement
        # exclusifs par CONTENEUR PARENT DIRECT - il n'existe pas de
        # propriete "GroupName" comme en WPF/HTML. Les 4 boutons (Nord +
        # Coordonnees) etaient auparavant ajoutes directement a card_coords,
        # formant donc UN SEUL groupe de 4 boutons exclusifs au lieu de deux
        # groupes independants de 2 : on ne pouvait choisir qu'UNE option au
        # total (nord OU coordonnees), jamais les deux. On isole chaque
        # paire dans son propre sous-panneau pour les rendre independantes.
        panel_north = WF.Panel()
        panel_north.SetBounds(0, 40, card_coords.Width, 50)
        card_coords.Controls.Add(panel_north)

        panel_coords = WF.Panel()
        panel_coords.SetBounds(0, 92, card_coords.Width, 58)
        card_coords.Controls.Add(panel_coords)

        lbl_north = WF.Label()
        lbl_north.Text = "Nord"
        lbl_north.SetBounds(16, 4, 120, 16)
        style_label_muted(lbl_north)
        panel_north.Controls.Add(lbl_north)

        self.rb_north_project = WF.RadioButton()
        self.rb_north_project.Text = "Projet (inchangé)"
        self.rb_north_project.Checked = True
        self.rb_north_project.SetBounds(16, 24, 220, 24)
        style_radio(self.rb_north_project)
        panel_north.Controls.Add(self.rb_north_project)

        self.rb_north_geo = WF.RadioButton()
        self.rb_north_geo.Text = "Géographique (source)"
        self.rb_north_geo.SetBounds(245, 24, 225, 24)
        style_radio(self.rb_north_geo)
        panel_north.Controls.Add(self.rb_north_geo)

        lbl_coords = WF.Label()
        lbl_coords.Text = "Coordonnées"
        lbl_coords.SetBounds(16, 4, 120, 16)
        style_label_muted(lbl_coords)
        panel_coords.Controls.Add(lbl_coords)

        self.rb_coord_internal = WF.RadioButton()
        self.rb_coord_internal.Text = "Internes (inchangé)"
        self.rb_coord_internal.Checked = True
        self.rb_coord_internal.SetBounds(16, 24, 220, 24)
        style_radio(self.rb_coord_internal)
        panel_coords.Controls.Add(self.rb_coord_internal)

        self.rb_coord_geo = WF.RadioButton()
        self.rb_coord_geo.Text = "Partagées (source)"
        self.rb_coord_geo.SetBounds(245, 24, 225, 24)
        style_radio(self.rb_coord_geo)
        panel_coords.Controls.Add(self.rb_coord_geo)

        # ---------------------------------------------------------------------
        # ACTION BAR
        # ---------------------------------------------------------------------
        action = WF.Panel()
        action.SetBounds(0, 820, 1024, 64)
        action.BackColor = COLOR_CARD
        action.Paint += self.on_action_paint
        self.Controls.Add(action)

        self.lbl_status = WF.Label()
        self.lbl_status.Text = "Prêt. Choisissez une source puis chargez les vues."
        self.lbl_status.Font = FONT_NORMAL
        self.lbl_status.ForeColor = COLOR_TEXT_MUTED
        self.lbl_status.SetBounds(24, 22, 620, 24)
        action.Controls.Add(self.lbl_status)

        btn_cancel = WF.Button()
        btn_cancel.Text = "Annuler"
        btn_cancel.DialogResult = WF.DialogResult.Cancel
        btn_cancel.SetBounds(690, 16, 140, 34)
        style_secondary_button(btn_cancel)
        action.Controls.Add(btn_cancel)

        btn_ok = WF.Button()
        btn_ok.Text = "Copier les vues"
        btn_ok.DialogResult = WF.DialogResult.None
        btn_ok.SetBounds(842, 16, 158, 34)
        style_primary_button(btn_ok)
        btn_ok.Click += self.on_ok_clicked
        action.Controls.Add(btn_ok)

        self.AcceptButton = btn_ok
        self.CancelButton = btn_cancel

        # ---------------------------------------------------------------------
        # ÉVÉNEMENTS
        # ---------------------------------------------------------------------
        self.rb_open.CheckedChanged += self.on_source_changed
        self.rb_link.CheckedChanged += self.on_source_changed
        self.rb_file.CheckedChanged += self.on_source_changed
        self.txt_path.TextChanged += self.on_path_changed
        self.txt_search.TextChanged += self.on_search_changed
        self.btn_browse.Click += self.on_browse
        self.btn_load.Click += self.on_load_views
        self.cbo_docs.SelectedIndexChanged += self.on_doc_selected
        self.cbo_filter.SelectedIndexChanged += self.on_filter_changed
        self.btn_select_all.Click += self.on_select_all
        self.btn_select_none.Click += self.on_select_none
        self.btn_invert.Click += self.on_invert_selection
        self.lv_views.ItemChecked += self.on_item_checked

    # -------------------------------------------------------------------------
    # UI HELPERS
    # -------------------------------------------------------------------------

    def add_card(self, x, y, w, h):
        card = WF.Panel()
        card.SetBounds(x, y, w, h)
        card.BackColor = COLOR_CARD
        card.Paint += self.on_card_paint
        self.Controls.Add(card)
        return card

    def add_card_title(self, card, text):
        lbl = WF.Label()
        lbl.Text = text
        lbl.Font = FONT_CARD_TITLE
        lbl.ForeColor = COLOR_TEXT_DARK
        lbl.SetBounds(16, 12, card.Width - 32, 22)
        card.Controls.Add(lbl)
        return lbl

    def on_card_paint(self, sender, e):
        try:
            pen = WD.Pen(COLOR_BORDER)
            e.Graphics.DrawRectangle(pen, 0, 0, sender.Width - 1, sender.Height - 1)
            pen.Dispose()
        except Exception:
            pass

    def on_action_paint(self, sender, e):
        try:
            pen = WD.Pen(COLOR_BORDER)
            e.Graphics.DrawLine(pen, 0, 0, sender.Width, 0)
            pen.Dispose()
        except Exception:
            pass

    def set_status(self, message, kind="info"):
        colors = {
            "info": COLOR_TEXT_MUTED,
            "success": COLOR_SUCCESS,
            "warning": COLOR_WARNING,
            "error": COLOR_ERROR
        }
        self.lbl_status.Text = message
        self.lbl_status.ForeColor = colors.get(kind, COLOR_TEXT_MUTED)

    def update_load_button_state(self, enabled):
        self.btn_load.Enabled = enabled
        try:
            if enabled:
                self.btn_load.BackColor = COLOR_ACCENT
                self.btn_load.ForeColor = WD.Color.White
                self.btn_load.Cursor = WF.Cursors.Hand
            else:
                self.btn_load.BackColor = COLOR_BORDER
                self.btn_load.ForeColor = COLOR_TEXT_MUTED
                self.btn_load.Cursor = WF.Cursors.Default
        except Exception:
            pass

    def update_status(self):
        total = len(self.view_meta)
        displayed = self.lv_views.Items.Count
        checked = len(self.checked_ids)

        try:
            self.lbl_views_title.Text = "3. Vues — {} chargées, {} affichées, {} cochées".format(
                total, displayed, checked
            )
        except Exception:
            pass

        try:
            source = self.current_source_doc.Title if self.current_source_doc else "aucune source chargée"
        except Exception:
            source = "source inconnue"

        self.set_status(
            "Source : {} | Vues : {} | Cochées : {}".format(source, total, checked),
            "info"
        )

    # -------------------------------------------------------------------------
    # SOURCES
    # -------------------------------------------------------------------------

    def set_sources(self, open_docs_map, link_docs_map):
        self.open_docs_map = open_docs_map or {}
        self.link_docs_map = link_docs_map or {}
        self.refresh_sources()

    def on_source_changed(self, sender, args):
        self.refresh_sources()

    def on_doc_selected(self, sender, args):
        if self.rb_open.Checked or self.rb_link.Checked:
            self.update_load_button_state(self.cbo_docs.SelectedItem is not None)

        self.clear_loaded_views()
        self.current_source_doc = None
        self.current_source_transform = None
        self.update_status()

    def on_path_changed(self, sender, args):
        if self.rb_file.Checked:
            self.update_load_button_state(self.txt_path.Text.strip() != "")

    def on_browse(self, sender, args):
        dlg = WF.OpenFileDialog()
        dlg.Filter = "Revit (*.rvt)|*.rvt|Tous les fichiers (*.*)|*.*"
        dlg.Title = "Choisir une maquette source"
        if dlg.ShowDialog() == WF.DialogResult.OK:
            self.txt_path.Text = dlg.FileName
            self.update_load_button_state(True)

    def refresh_sources(self):
        self.cbo_docs.Items.Clear()
        self.clear_loaded_views()
        self.current_source_doc = None
        self.current_source_transform = None

        if self.rb_open.Checked:
            for key in sorted(self.open_docs_map.keys()):
                self.cbo_docs.Items.Add(key)

            self.cbo_docs.Visible = True
            self.txt_path.Visible = False
            self.btn_browse.Visible = False
            self.cbo_docs.Enabled = len(self.open_docs_map) > 0
            self.update_load_button_state(False)

        elif self.rb_link.Checked:
            for key in sorted(self.link_docs_map.keys()):
                self.cbo_docs.Items.Add(key)

            self.cbo_docs.Visible = True
            self.txt_path.Visible = False
            self.btn_browse.Visible = False
            self.cbo_docs.Enabled = len(self.link_docs_map) > 0
            self.update_load_button_state(False)

        else:
            self.cbo_docs.Visible = False
            self.txt_path.Visible = True
            self.btn_browse.Visible = True
            self.cbo_docs.Enabled = False
            self.update_load_button_state(self.txt_path.Text.strip() != "")

        if self.cbo_docs.Visible and self.cbo_docs.Items.Count > 0:
            self.cbo_docs.SelectedIndex = 0

        self.update_status()

    # -------------------------------------------------------------------------
    # CHARGEMENT DES VUES
    # -------------------------------------------------------------------------

    def on_load_views(self, sender, args):
        try:
            self.update_load_button_state(False)
            self.set_status("Chargement en cours...", "info")
            WF.Application.DoEvents()

            src_doc = None
            src_transform = None

            if not self.rb_file.Checked and self.background_doc is not None:
                self.close_background_doc()

            if self.rb_open.Checked:
                key = self.cbo_docs.SelectedItem
                if key is not None:
                    src_doc = self.open_docs_map.get(key)

            elif self.rb_link.Checked:
                key = self.cbo_docs.SelectedItem
                if key is not None:
                    pair = self.link_docs_map.get(key)
                    if pair is not None:
                        src_doc, src_transform = pair

            else:
                path = self.txt_path.Text.strip()
                if not path:
                    self.set_status("Choisissez un fichier .rvt.", "error")
                    return

                if self.background_doc is not None:
                    try:
                        bg_path = self.background_doc.PathName
                    except Exception:
                        bg_path = None

                    if bg_path and are_same_path(bg_path, path):
                        src_doc = self.background_doc
                    else:
                        self.close_background_doc()
                        existing = find_open_document_by_path(path)

                        if existing is not None:
                            src_doc = existing
                        else:
                            try:
                                TransactionManager.Instance.ForceCloseTransaction()
                            except Exception:
                                pass

                            src_doc, newly_opened = open_background_document(path)
                            if newly_opened:
                                self.background_doc = src_doc
                else:
                    existing = find_open_document_by_path(path)
                    if existing is not None:
                        src_doc = existing
                    else:
                        try:
                            TransactionManager.Instance.ForceCloseTransaction()
                        except Exception:
                            pass

                        src_doc, newly_opened = open_background_document(path)
                        if newly_opened:
                            self.background_doc = src_doc

            if src_doc is None:
                self.set_status("Impossible de charger le document source.", "error")
                return

            if is_current_document(src_doc):
                self.set_status("La source ne peut pas être le document cible.", "error")
                return

            try:
                if src_doc.IsFamilyDocument:
                    self.set_status("Le fichier source est un fichier de famille.", "error")
                    return
            except Exception:
                pass

            self.current_source_doc = src_doc
            self.current_source_transform = src_transform
            self.load_views_from_doc(src_doc)

        except Exception as ex:
            self.set_status("Erreur : {}".format(ex), "error")
        finally:
            if self.rb_file.Checked:
                self.update_load_button_state(self.txt_path.Text.strip() != "")
            elif self.cbo_docs.SelectedItem is not None:
                self.update_load_button_state(True)
            else:
                self.update_load_button_state(False)

    def load_views_from_doc(self, src_doc):
        self.clear_loaded_views()

        try:
            views = FilteredElementCollector(src_doc).OfClass(View)
            count = 0

            for view in views:
                try:
                    if view.IsTemplate:
                        continue

                    vt = view.ViewType
                    if vt not in VIEW_TYPE_LABELS:
                        continue

                    try:
                        name = str(view.Name)
                    except Exception:
                        name = "Sans nom"

                    type_label = VIEW_TYPE_LABELS.get(vt, str(vt))

                    try:
                        scale = str(view.Scale)
                    except Exception:
                        scale = "-"

                    level_name = "-"
                    if isinstance(view, ViewPlan):
                        try:
                            level_name = str(view.GenLevel.Name)
                        except Exception:
                            level_name = "-"

                    id_int = int(id_value(view.Id))
                    self.view_data[id_int] = view

                    self.view_meta.append({
                        "id": id_int,
                        "name": name,
                        "type_label": type_label,
                        "scale": scale,
                        "level": level_name,
                        "view_type": vt
                    })

                    count += 1
                except Exception:
                    continue

            self.refresh_list_view()

            if count == 0:
                self.set_status("Aucune vue exploitable trouvée.", "warning")
            else:
                self.set_status("{} vues chargées. Cochez les vues à copier.".format(count), "success")

        except Exception as ex:
            self.set_status("Erreur lors du chargement des vues : {}".format(ex), "error")

    # -------------------------------------------------------------------------
    # LISTE DES VUES
    # -------------------------------------------------------------------------

    def clear_loaded_views(self):
        self.lv_views.Items.Clear()
        self.view_data.clear()
        self.view_meta = []
        self.checked_ids.clear()

    def matches_filter(self, view_type):
        selected_filter = self.cbo_filter.SelectedItem
        if selected_filter is None or selected_filter == FILTER_ALL:
            return True

        allowed = VIEW_GROUPS.get(selected_filter, tuple())
        return view_type in allowed

    def matches_search(self, name):
        text = self.txt_search.Text.strip().lower()
        if not text:
            return True
        return text in str(name).lower()

    def refresh_list_view(self):
        self._suppress_check_events = True
        try:
            self.lv_views.BeginUpdate()
            self.lv_views.Items.Clear()

            for meta in self.view_meta:
                if self.matches_filter(meta["view_type"]) and self.matches_search(meta["name"]):
                    item = WF.ListViewItem(meta["name"])
                    item.SubItems.Add(meta["type_label"])
                    item.SubItems.Add(meta["scale"])
                    item.SubItems.Add(meta["level"])
                    item.Tag = meta["id"]
                    item.Checked = meta["id"] in self.checked_ids
                    self.lv_views.Items.Add(item)

            self.lv_views.EndUpdate()
        finally:
            self._suppress_check_events = False

        self.update_status()

    def on_search_changed(self, sender, args):
        self.refresh_list_view()

    def on_filter_changed(self, sender, args):
        self.refresh_list_view()

    def on_item_checked(self, sender, e):
        if self._suppress_check_events:
            return

        try:
            id_val = e.Item.Tag
            if id_val is None:
                return

            if e.Item.Checked:
                self.checked_ids.add(id_val)
            else:
                self.checked_ids.discard(id_val)

            self.update_status()
        except Exception:
            pass

    def on_select_all(self, sender, args):
        self._suppress_check_events = True
        try:
            for item in self.lv_views.Items:
                item.Checked = True
                self.checked_ids.add(item.Tag)
        finally:
            self._suppress_check_events = False
        self.update_status()

    def on_select_none(self, sender, args):
        self._suppress_check_events = True
        try:
            for item in self.lv_views.Items:
                item.Checked = False
                self.checked_ids.discard(item.Tag)
        finally:
            self._suppress_check_events = False
        self.update_status()

    def on_invert_selection(self, sender, args):
        self._suppress_check_events = True
        try:
            for item in self.lv_views.Items:
                new_state = not item.Checked
                item.Checked = new_state

                if new_state:
                    self.checked_ids.add(item.Tag)
                else:
                    self.checked_ids.discard(item.Tag)
        finally:
            self._suppress_check_events = False
        self.update_status()

    def get_selected_views(self):
        selected = []
        for meta in self.view_meta:
            if meta["id"] in self.checked_ids:
                view = self.view_data.get(meta["id"])
                if view is not None:
                    selected.append(view)
        return selected

    # -------------------------------------------------------------------------
    # VALIDATION / SORTIE
    # -------------------------------------------------------------------------

    def on_ok_clicked(self, sender, args):
        # En plus du texte de statut (petit, en bas a gauche, facile a
        # manquer), on affiche une popup pour ces deux cas de validation :
        # sans ca, cliquer sur "Copier les vues" sans source chargee ou
        # sans vue cochee ne montrait qu'un discret changement de texte,
        # ce qui donnait l'impression que le bouton ne faisait rien du tout.
        if self.current_source_doc is None:
            self.set_status("Chargez d'abord un document source.", "error")
            WF.MessageBox.Show(
                "Aucun document source n'est charge.\n\nChoisissez une source (1) puis cliquez sur \"Charger les vues\" avant de cliquer sur \"Copier les vues\".",
                "Aucune source chargee",
                WF.MessageBoxButtons.OK,
                WF.MessageBoxIcon.Warning
            )
            return

        if not self.get_selected_views():
            self.set_status("Sélectionnez au moins une vue à copier.", "error")
            WF.MessageBox.Show(
                "Aucune vue n'est cochee dans la liste.\n\nCochez au moins une vue (section 3) avant de cliquer sur \"Copier les vues\".",
                "Aucune vue selectionnee",
                WF.MessageBoxButtons.OK,
                WF.MessageBoxIcon.Warning
            )
            return

        self.DialogResult = WF.DialogResult.OK
        self.Close()

    def close_background_doc(self):
        bg = self.background_doc
        self.background_doc = None

        if bg is not None:
            try:
                if self.current_source_doc is not None and are_same_document(self.current_source_doc, bg):
                    self.current_source_doc = None
                    self.current_source_transform = None
            except Exception:
                pass

            close_document_quiet(bg)

    def get_selection(self):
        return {
            "source_doc": self.current_source_doc,
            "source_transform": self.current_source_transform,
            "selected_views": self.get_selected_views(),
            "north_geo": self.rb_north_geo.Checked,
            "coords_geo": self.rb_coord_geo.Checked,
            "background_doc": self.background_doc if are_same_document(self.current_source_doc, self.background_doc) else None,
            "copy_annotations": self.chk_copy_annotations.Checked,
            "copy_templates": self.chk_copy_templates.Checked,
            "copy_graphics": self.chk_copy_graphics.Checked
        }


# =============================================================================
# PROGRAMME PRINCIPAL
# =============================================================================

def main():
    form = None

    try:
        open_docs_map = collect_open_documents()
        link_docs_map = collect_link_documents()

        form = CopyViewsForm(doc.Title)
        form.set_sources(open_docs_map, link_docs_map)

        if form.ShowDialog() != WF.DialogResult.OK:
            form.close_background_doc()
            return "Opération annulée."

        selection = form.get_selection()

        src_doc = selection.get("source_doc")
        background_doc = selection.get("background_doc")

        if src_doc is None:
            form.close_background_doc()
            return "Aucun document source chargé."

        selected_views = selection.get("selected_views") or []
        if not selected_views:
            form.close_background_doc()
            return "Aucune vue sélectionnée. Cochez au moins une vue à copier."

        try:
            source_title = src_doc.Title
        except Exception:
            source_title = "Document source"

        copied_views = []
        recreated_views = []
        ignored_views = []
        warnings = []

        annotation_count = 0
        template_cache = {}
        filter_cache = {}
        level_map = build_level_map(doc)
        view_names = get_existing_view_names(doc)
        position_message = ""

        error_text = None

        try:
            TransactionManager.Instance.EnsureInTransaction(doc)

            if selection.get("north_geo") or selection.get("coords_geo"):
                ok, err = transfer_project_position(
                    src_doc,
                    doc,
                    selection.get("north_geo", False),
                    selection.get("coords_geo", False)
                )

                if ok:
                    parts = []
                    if selection.get("north_geo"):
                        parts.append("nord")
                    if selection.get("coords_geo"):
                        parts.append("coordonnées")
                    position_message = "Transfert {} depuis la source".format(" et ".join(parts))
                else:
                    position_message = "Erreur de transfert de position"
                    warnings.append("Position : {}".format(err))

            for view in selected_views:
                try:
                    view_name = str(view.Name)
                except Exception:
                    view_name = "Vue inconnue"

                try:
                    vt = view.ViewType

                    if vt in DIRECT_COPY_TYPES:
                        new_view, err = copy_direct_view(
                            view,
                            doc,
                            template_cache,
                            view_names,
                            selection.get("copy_graphics", True),
                            selection.get("copy_templates", True),
                            selection.get("source_transform"),
                            filter_cache
                        )

                        if new_view is None:
                            ignored_views.append((view_name, err or "Erreur inconnue"))
                        else:
                            copied_views.append(view_name)

                    else:
                        new_view = None
                        err = None

                        if isinstance(view, ViewPlan):
                            new_view, err = recreate_plan_view(view, doc, level_map)

                        elif isinstance(view, ViewSection):
                            new_view, err = recreate_section_view(
                                view,
                                doc,
                                selection.get("source_transform")
                            )

                        elif isinstance(view, View3D):
                            new_view, err = recreate_3d_view(
                                view,
                                doc,
                                selection.get("source_transform")
                            )

                        elif vt == ViewType.Elevation:
                            err = "Les élévations ne peuvent pas être recréées automatiquement par ce script"

                        else:
                            err = "Type de vue non pris en charge"

                        if new_view is None:
                            ignored_views.append((view_name, err or "Erreur inconnue"))
                        else:
                            apply_view_properties(
                                view,
                                new_view,
                                doc,
                                template_cache,
                                view_names,
                                selection.get("copy_graphics", True),
                                selection.get("copy_templates", True),
                                selection.get("source_transform"),
                                filter_cache
                            )

                            if selection.get("copy_annotations", True):
                                count, ann_err = copy_view_specific_elements(view, new_view)
                                annotation_count += count

                                if ann_err:
                                    warnings.append("Annotations de '{}' : {}".format(view_name, ann_err))

                            recreated_views.append(view_name)

                except Exception as ex:
                    ignored_views.append((view_name, str(ex)))

            TransactionManager.Instance.TransactionTaskDone()

        except Exception as ex:
            error_text = "{}\n{}".format(ex, traceback.format_exc())
            try:
                TransactionManager.Instance.ForceCloseTransaction()
            except Exception:
                pass

        finally:
            close_document_quiet(background_doc)

        if error_text is not None:
            WF.MessageBox.Show(
                "La copie a echoue :\n\n{}".format(error_text),
                "Erreur",
                WF.MessageBoxButtons.OK,
                WF.MessageBoxIcon.Error
            )
            return "Échec de la copie : {}".format(error_text)

        report = build_report(
            source_title,
            doc.Title,
            len(selected_views),
            selection.get("north_geo", False),
            selection.get("coords_geo", False),
            copied_views,
            recreated_views,
            ignored_views,
            warnings,
            annotation_count,
            position_message
        )

        # Resume visible immediatement (popup), en plus du rapport complet
        # renvoye dans OUT : sans ca, tout le resultat de l'operation n'etait
        # visible qu'en ouvrant la bulle de sortie du noeud Dynamo, ce qui
        # donnait l'impression que rien ne s'etait passe si on ne pensait
        # pas a la regarder.
        summary_kind = WF.MessageBoxIcon.Information if not ignored_views else WF.MessageBoxIcon.Warning

        summary_text = "Copie terminee.\n\nCopiees directement : {}\nRecreees : {}\nIgnorees : {}".format(
            len(copied_views), len(recreated_views), len(ignored_views)
        )

        # La raison de chaque vue ignoree n'apparaissait auparavant que dans
        # le rapport complet (sortie OUT du noeud), facile a ne pas ouvrir.
        # On l'affiche directement dans la popup pour eviter un aller-retour.
        if ignored_views:
            summary_text += "\n\nDetail des vues ignorees :"
            for name, reason in ignored_views[:15]:
                summary_text += "\n  - {} : {}".format(name, reason)
            if len(ignored_views) > 15:
                summary_text += "\n  ... et {} autre(s) (voir le rapport complet dans OUT).".format(len(ignored_views) - 15)

        summary_text += "\n\n(Rapport complet dans la sortie OUT du noeud.)"

        WF.MessageBox.Show(
            summary_text,
            "Copie des vues",
            WF.MessageBoxButtons.OK,
            summary_kind
        )

        return report

    except Exception as ex:
        try:
            if form is not None:
                form.close_background_doc()
        except Exception:
            pass

        error_text = "{}\n{}".format(ex, traceback.format_exc())
        try:
            WF.MessageBox.Show(
                "Erreur inattendue :\n\n{}".format(error_text),
                "Erreur",
                WF.MessageBoxButtons.OK,
                WF.MessageBoxIcon.Error
            )
        except Exception:
            pass

        return "Erreur : {}".format(error_text)


OUT = main()