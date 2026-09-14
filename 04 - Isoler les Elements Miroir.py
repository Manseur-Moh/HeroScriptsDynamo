# ============================================================
# 🎩 by Manseur Mohamed
# Script Dynamo Python - Isoler les elements miroir
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
# ses propres symboles (dont "View") qui peuvent ecraser ceux de
# Autodesk.Revit.DB. On fige les references dont on a besoin avant le
# wildcard import suivant (meme convention que les autres scripts du projet).
RevitView = View
RevitTransaction = Transaction

from System.Windows.Forms import *
from System.Drawing import *
from System.Collections.Generic import List

doc = DocumentManager.Instance.CurrentDBDocument
uidoc = DocumentManager.Instance.CurrentUIApplication.ActiveUIDocument

# ============================================================
# ️ INTERFACE GRAPHIQUE (WinForms) - AJUSTÉE
# ============================================================
class FormMiroir(Form):
    def __init__(self):
        Form.__init__(self)
        self.InitializeComponent()
        
        self.mirrored_ids = []
        self.mirrored_count = 0

    def InitializeComponent(self):
        self.Text = "04 - Isoler les Éléments Miroir - 🎩 by Manseur Mohamed"
        self.Size = Size(550, 520)  # Hauteur globale ajustée
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.BackColor = Color.FromArgb(240, 240, 240)

        # Titre
        lbl_title = Label()
        lbl_title.Text = "Isoler les Éléments Miroir"
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
        lbl_sig.Location = Point(380, 20)
        lbl_sig.AutoSize = True
        self.Controls.Add(lbl_sig)

        # Info
        lbl_info = Label()
        lbl_info.Text = "Ce script va isoler temporairement les éléments miroir dans la vue active."
        lbl_info.Font = Font("Segoe UI", 10)
        lbl_info.Location = Point(20, 65)
        lbl_info.Size = Size(490, 40)
        self.Controls.Add(lbl_info)

        # Bouton principal
        self.btn_find = Button()
        self.btn_find.Text = "Isoler les Miroirs dans la Vue Active"
        self.btn_find.Font = Font("Segoe UI", 11, FontStyle.Bold)
        self.btn_find.Location = Point(20, 120)
        self.btn_find.Size = Size(490, 50)
        self.btn_find.BackColor = Color.FromArgb(0, 120, 215)
        self.btn_find.ForeColor = Color.White
        self.btn_find.FlatStyle = FlatStyle.Flat
        self.btn_find.FlatAppearance.BorderSize = 0
        self.btn_find.Click += self.find_and_prepare
        self.Controls.Add(self.btn_find)

        # Journal d'exécution
        lbl_log = Label()
        lbl_log.Text = "Journal d'exécution :"
        lbl_log.Font = Font("Segoe UI", 10, FontStyle.Bold)
        lbl_log.Location = Point(20, 190)
        lbl_log.Size = Size(490, 20)
        self.Controls.Add(lbl_log)

        self.txt_log = TextBox()
        self.txt_log.Font = Font("Consolas", 9)
        self.txt_log.Multiline = True
        self.txt_log.ReadOnly = True
        self.txt_log.ScrollBars = ScrollBars.Vertical
        self.txt_log.Location = Point(20, 215)
        self.txt_log.Size = Size(490, 190)  # Hauteur ajustée pour laisser de la place en bas
        self.Controls.Add(self.txt_log)

        # Bouton Fermer (Remonté pour être sûr d'être visible)
        btn_close = Button()
        btn_close.Text = "Fermer"
        btn_close.Font = Font("Segoe UI", 10, FontStyle.Bold)
        btn_close.Location = Point(200, 425)  # Remonté à 425
        btn_close.Size = Size(130, 35)
        btn_close.BackColor = Color.FromArgb(100, 100, 100)
        btn_close.ForeColor = Color.White
        btn_close.FlatStyle = FlatStyle.Flat
        btn_close.FlatAppearance.BorderSize = 0
        btn_close.Click += lambda s, e: self.Close()
        self.Controls.Add(btn_close)

    def log(self, message):
        self.txt_log.AppendText(message + "\r\n")

    def find_and_prepare(self, sender, args):
        self.txt_log.Clear()
        self.mirrored_ids = []

        try:
            # Collecte des FamilyInstances miroir
            collector = FilteredElementCollector(doc).OfClass(FamilyInstance)
            # try/except PAR ELEMENT : .Mirrored peut lever une exception sur
            # certaines instances (familles in-situ, geometrie non resolue).
            # Sans ce garde-fou, un seul element defaillant faisait echouer
            # toute la collecte et le script ne trouvait plus aucun miroir.
            mirrored_elements = []
            for e in collector:
                try:
                    if e.Mirrored:
                        mirrored_elements.append(e)
                except Exception:
                    continue

            if not mirrored_elements:
                self.log("Aucun élément miroir trouvé.")
                MessageBox.Show("Aucun élément miroir trouvé dans le projet.", 
                                "Information", MessageBoxButtons.OK, MessageBoxIcon.Information)
                return

            self.mirrored_count = len(mirrored_elements)
            self.mirrored_ids = [e.Id for e in mirrored_elements]
            self.log(str(self.mirrored_count) + " élément(s) miroir trouvé(s).")
            self.log("Préparation de l'isolation dans la vue active...")

            # Validation et fermeture
            self.DialogResult = DialogResult.OK
            self.Close()

        except Exception as ex:
            self.log("Erreur lors de la collecte : " + str(ex))

# ============================================================
# 🚀 EXÉCUTION PRINCIPALE & TRANSACTION REVIT
# ============================================================
form = FormMiroir()
dialog_result = form.ShowDialog()

OUT = None

if dialog_result == DialogResult.OK and form.mirrored_ids:
    ids_list = List[ElementId](form.mirrored_ids)
    
    t = RevitTransaction(doc, "Isoler éléments miroir")
    try:
        t.Start()
        
        active_view = uidoc.ActiveView
        
        # Réinitialise un isolement temporaire précédent si existant
        try:
            active_view.ResetTemporaryHideIsolate()
        except:
            pass
            
        # Isolation temporaire des éléments miroir
        active_view.IsolateElementsTemporary(ids_list)

        # Commit AVANT toute autre operation : les appels form.log() qui
        # suivaient etaient dans le try, donc la moindre erreur d'affichage
        # sur la fenetre deja fermee partait dans le except et declenchait un
        # RollBack() qui annulait un isolement pourtant reussi.
        t.Commit()

        OUT = "Succès : " + str(form.mirrored_count) + " éléments isolés dans la vue " + active_view.Name
        try:
            form.log("Éléments isolés avec succès dans la vue : " + active_view.Name)
            form.log("(Pour retirer l'isolement : icône ampoule en bas de la vue)")
        except Exception:
            pass

    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        OUT = "Erreur : " + str(ex)
        try:
            form.log("Erreur lors de la transaction : " + str(ex))
        except Exception:
            pass
else:
    OUT = "Opération annulée."