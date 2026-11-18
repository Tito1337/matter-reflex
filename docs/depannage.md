# Dépanner une automatisation Reflex

| Observation | Vérification |
| --- | --- |
| HA découvre les appareils, mais le formulaire Reflex ne propose aucune carte | Vérifier que le XIAO est commissionné dans l’intégration Matter de cette instance et que le firmware Reflex est installé. La console USB doit indiquer `thread=attached` et une fabric. |
| **Synchronisé** est actif, mais un raccourci manque | Ouvrir **Paramètres → Système → Réparations**. Une automatisation refusée est exclue en entier de la table synchronisée. Vérifier aussi le label de l’automatisation, son activation et le type d’événement annoncé par le bouton. |
| **Synchronisé** reste éteint | Lire le motif de synchronisation dans Réparations. Vérifier la disponibilité du serveur et des endpoints concernés. En cas de refus ACL, examiner la capacité ou les changements concurrents avant de modifier les permissions. |
| Un appui pendant une coupure ne fait rien | Lire `./dev usb python /work/tools/board_console.py status`. Attendre `active=1` avant l’appui. Contrôler le compteur juste avant et juste après le geste. Le rétablissement automatique du serveur peut avoir renouvelé le bail. |
| Le compteur augmente, mais la cible ne réagit pas | Le compteur inclut les résultats en erreur et une règle par cible. Examiner le statut de `RuleExecuted` si le contrôleur le rend disponible. Les valeurs locales 0x100, 0x101 et 0x102 indiquent respectivement une session absente, un état inconnu et une erreur de transport. |
| Le XIAO tarde à reprendre la main après une perte radio | Le firmware conserve les sessions et l’adresse du pair en RAM. Après un redémarrage ou une perte de route, la reconnexion dépend de Thread et de la découverte encore disponibles. |

## Télécharger le diagnostic dans Home Assistant

Dans **Paramètres → Appareils et services → Matter Reflex**, ouvre le menu de l’entrée puis **Télécharger les diagnostics**. Sa section `data` donne le transport de synchronisation (`matter` ou `http`), la disponibilité du dernier statut, le mode Reflex, la synchronisation des règles et le nombre de résultats signalés depuis le démarrage du XIAO. Une valeur `null` signifie que l’information manque. Pendant une coupure du serveur, la console USB donne accès directement à l’état de la carte.

Pour un signalement public, joins la section `data`. Home Assistant ajoute des métadonnées et des réparations au fichier complet, qui peuvent contenir des noms ou des identifiants de ton installation.

## Confirmer un essai de panne

Commencer sur une cible dont le changement est visible. Relever l’état initial, l’empreinte de la table et le compteur en USB. Pendant une fenêtre de panne maîtrisée, vérifier `active=1`, appuyer et observer le résultat physique. À la reprise, vérifier le retour à `active=0`, la synchronisation dans HA et le rétablissement de l’appareil.

Un appui qui commande deux lumières et un store peut produire trois résultats. Si un compteur dépasse le total attendu, noter les appuis et les heures de part et d’autre de la reprise. Un compteur seul ne permet pas d’attribuer un résultat à un geste.
