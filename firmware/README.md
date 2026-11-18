# Firmware du XIAO ESP32-C6

Le lanceur du dépôt utilise l’image officielle Espressif `release-v1.6_idf_v5.5.4`
épinglée par digest dans `dev`. Elle contient ESP-IDF 5.5.4, ESP-Matter
`33b042e85360dd7a352167a6cba204f7087eba67` et connectedhomeip
`efefc94fee39d8d1fbbc3c27b9d7fc9025095887`.
[Exemple upstream light_switch](https://github.com/espressif/esp-matter/tree/33b042e85360dd7a352167a6cba204f7087eba67/examples/light_switch)

## Compiler

Depuis la racine du dépôt :

```sh
./dev matter idf.py -C firmware build
./dev matter idf.py -C firmware size
```

Le binaire applicatif est `firmware/build/matter_reflex.bin`. La table
`partitions.csv` lui réserve 4 063 232 octets. Le reste des 4 Mio contient le
chargeur, la table de partitions, les données radio, NVS et la partition
d’usine `fctry`. Le profil active le routeur OpenThread FTD, le client
SRP/DNS, l’IPv6 et la console USB Serial/JTAG. La radio utilise 802.15.4.
La mise à jour applicative passe par USB.

## Sélectionner la carte USB

Branche le XIAO avec un câble de données. La commande suivante affiche les identifiants stables des périphériques série.

```sh
ls -l /dev/serial/by-id/
```

Repère le lien correspondant à ta carte, puis remplace `IDENTIFIANT_DU_XIAO` dans la commande suivante. Le lanceur garde l’identité d’appairage associée au nom de ce lien. Sur une machine avec plusieurs cartes, débrancher puis rebrancher le XIAO permet de l’identifier.

```sh
git config --local matter-reflex.board '/dev/serial/by-id/IDENTIFIANT_DU_XIAO'
```

Pour une autre carte ou pour une copie du projet obtenue sans Git, définis `MATTER_REFLEX_BOARD` avec le chemin complet du lien USB. Cette variable est prioritaire. L’utilisateur qui lance Podman doit avoir les droits de lecture et d’écriture sur le périphérique série. La compilation seule fonctionne sans carte connectée.

## Préparer le premier appairage

```sh
./dev factory
```

Cette commande génère une identité d’appairage dédiée à la carte sélectionnée
via son identifiant USB. Elle utilise le générateur officiel de CHIP
et conserve le même code aux exécutions suivantes. Le fichier privé
`onboarding_codes.csv` contient le QR Matter et le code manuel de l’instance.
La partition `factory_partition.bin` contient le vérificateur SPAKE2+.
Les deux fichiers restent dans le dossier privé
`${XDG_DATA_HOME:-$HOME/.local/share}/matter-reflex/<identifiant-USB>/factory/`.
Dans le nom du dossier les deux-points du numéro de série deviennent des tirets.

La carte utilise l’attestation de développement CHIP, le VID de test `0xFFF1`
et le PID `0x8000`. Le contrôleur de commissioning doit accepter cette
attestation de test. Le produit s’annonce sous le nom « Matter Reflex ».

## Installer sur la carte

Les commandes suivantes modifient la mémoire flash. Pour une première
installation sur une carte déjà programmée, sauvegarder d’abord **ses 4 Mio
entiers** dans un emplacement privé. La nouvelle table de partitions attribue
les zones de flash à Matter Reflex. Le port est résolu par identifiant USB par
`./dev matter-usb`.

Pour créer la sauvegarde depuis la racine du clone, ces commandes placent une
copie complète dans un dossier privé ignoré par Git et vérifient sa longueur.

```sh
./dev usb bash -ec 'umask 077; mkdir -p private; dir=$(mktemp -d private/xiao.XXXXXX); python -m esptool --chip esp32c6 -p "$ESPPORT" read_flash 0 0x400000 "$dir/flash.bin" && test "$(wc -c < "$dir/flash.bin")" -eq 4194304 && echo "$dir/flash.bin"'
```

1. Écrire le chargeur, la table et le firmware.
2. Écrire les données usine générées pour cette carte dans la partition
   `fctry`, localisée par son nom dans la table sur la carte.
3. Provisionner le dataset avec le lecteur série de la section suivante.

```sh
./dev matter-usb idf.py -C firmware flash
./dev matter-usb bash -c 'python "$IDF_PATH/components/partition_table/parttool.py" --port "$ESPPORT" --partition-table-offset 0xc000 write_partition --partition-name fctry --input /factory/factory_partition.bin'
```

Pour les mises à jour du même profil de partitions, utiliser
`./dev matter-usb idf.py -C firmware app-flash` pour écrire uniquement l’application.
La permission de lecture/écriture du périphérique USB peut être redonnée après
reconnexion par les mécanismes habituels de la distribution Linux.

Pour revenir du firmware 1.1 au 1.0 après utilisation de règles de store, retirer
leur label `reflex` et attendre que HA confirme la synchronisation avant le flash.
Le firmware 1.0 ne peut charger qu’une image contenant exclusivement des règles lumière.

## Rattacher le XIAO à Thread

Ouvre **Paramètres → Appareils et services → Thread → Configurer** avec un
compte administrateur. Vérifie que le réseau est celui des télécommandes. Si
Home Assistant n’a pas ses identifiants, importe-les depuis le téléphone qui
connaît le réseau. [Gestion des identifiants Thread](https://www.home-assistant.io/integrations/thread/)

Si la fiche du réseau affiche les TLV du dataset actif, copie-les. Pour les
routeurs de bordure externes, tu peux aussi les obtenir via le WebSocket de
ta session HA. Depuis la console développeur de ton navigateur, sur la page
HA, exécute la première commande pour choisir un réseau :

```js
const ha = document.querySelector("home-assistant").hass
const { datasets } = await ha.callWS({ type: "thread/list_datasets" })
console.table(datasets.map(({ network_name, dataset_id, preferred }) => ({ network_name, dataset_id, preferred })))
```

La commande suivante présente le TLV dans une boîte de dialogue pour le
copier. Le dataset contient la clé d’accès à ton réseau Thread.

```js
const dataset_id = prompt("Identifiant du réseau choisi dans le tableau")
if (dataset_id) {
  const { tlv } = await ha.callWS({ type: "thread/get_dataset_tlv", dataset_id })
  void prompt("Copie ce dataset dans la saisie masquée du terminal.", tlv)
}
```

Dans un terminal, colle le TLV à l’invite masquée, puis vérifie le rattachement :

```sh
./dev usb python /work/tools/board_console.py dataset
./dev usb python /work/tools/board_console.py status
```

Le lecteur accepte aussi la réponse WebSocket JSON complète par redirection
depuis un fichier privé. Le XIAO conserve le dataset et ouvre une fenêtre de
commissionnement de 15 minutes. Attends que `status` affiche `thread=attached`.
Si la fenêtre expire, `./dev usb python /work/tools/board_console.py pair`
en ouvre une autre avant le premier appairage.

## Appairer avec Home Assistant

Dans la console du navigateur sur HA, lance cette commande. À l’invite,
colle la valeur de la colonne `qrcode` de ton fichier privé
`onboarding_codes.csv` (la valeur commence par `MT:`).

```js
const pairingCode = prompt("Code QR Matter du XIAO")
if (pairingCode) {
  await document.querySelector("home-assistant").hass.callWS({
    type: "matter/commission", code: pairingCode, network_only: true
  })
}
```

Vérifie que **Matter Reflex** apparaît dans l’intégration Matter et que la
commande série `status` indique `fabrics=1` et un node ID. Ferme la console
du navigateur après l’opération. Le profil fourni utilise Thread pour ce
commissionnement. [Client WebSocket de Home Assistant](https://developers.home-assistant.io/docs/api/websocket/)

Lorsque le formulaire Matter Reflex demande la clé de transfert, crée dans
ton gestionnaire de mots de passe une valeur aléatoire de **64 chiffres
hexadécimaux** (32 octets). Transmets la même valeur à l’invite masquée
suivante puis au formulaire. Le premier statut authentifié lie la clé au
nœud et à sa fabric.

```sh
./dev usb python /work/tools/board_console.py key
```

Le bail standard arrive sur l’endpoint 2 (On/Off Plug-in Unit logique).
Une commande CASE `OnWithTimedOff` d’administration autorise HA à inhiber
le moteur local durant la durée indiquée. L’endpoint 1 porte le cluster de
règles et l’événement fabricant `RuleExecuted`. Ses champs 0 à 3 sont l’ID
de règle, le node ID local, le numéro d’événement source et le statut de la
commande. Les statuts locaux 0x100, 0x101 et 0x102 indiquent respectivement
une session absente, un état inconnu et une erreur de transport.

Lors d’une perte du border router, les sessions CASE actives et la dernière
adresse authentifiée de chaque pair peuvent encore servir. Ce cache est en RAM.
Après perte simultanée du cache et du service de découverte Thread, la
reconnexion dépend des services encore présents sur le maillage.
