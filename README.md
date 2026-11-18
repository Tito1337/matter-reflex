# Matter Reflex

Une télécommande peut continuer à commander une lampe, une prise ou un store pendant une panne de Home Assistant ou de son serveur Matter. Matter Reflex associe une intégration Home Assistant à un **Seeed Studio XIAO ESP32-C6** sur le réseau Thread. Tu écris l’automatisation dans HA et tu lui donnes le label `reflex`. Le XIAO en conserve les règles et prend le relais après expiration d’un bail de dix secondes.

## Un premier raccourci

Cet exemple allume une lampe à 70 % sur un double appui. Dans l’éditeur YAML d’une automatisation HA, remplace les deux entités par celles de ta télécommande et de ta lampe. Vérifie que le bouton annonce `multi_press_2` parmi ses types d’événements.

```yaml
alias: Double appui pour lire
triggers:
  - trigger: event.received
    target:
      entity_id: event.bouton_lecture
    options:
      event_type: [multi_press_2]
actions:
  - action: light.turn_on
    target:
      entity_id: light.lampe_lecture
    data:
      brightness_pct: 70
mode: single
```

Après installation, ajoute le label **reflex** à l’automatisation activée. Vérifie le capteur **Synchronisé** et la page **Réparations**. Le capteur confirme le transfert de la table acceptée. Une automatisation incompatible est signalée individuellement dans Réparations.

## Compatibilité

Le déclencheur est un événement de bouton Matter (`event.received`). Chaque cible est une entité explicite de la même fabric Matter, le domaine de confiance dans lequel HA a appairé les appareils.

| Destination | Actions prises en charge |
| --- | --- |
| Lumière | `light.turn_on`, `light.turn_off`, `light.toggle`. À l’allumage, fixer la luminosité ou la modifier par pas, en unités HA ou en pourcentage entier. |
| Prise | `switch.turn_on`, `switch.turn_off`, `switch.toggle` sur la sortie d’une prise Matter. |
| Store | `cover.open_cover`, `cover.close_cover`, `cover.stop_cover`, `cover.set_cover_position`, selon les capacités annoncées. La position HA va de 0 (fermé) à 100 (ouvert). |

La table contient jusqu’à **32 associations**. Un geste qui commande trois appareils en utilise trois. Une bascule exige que le XIAO ait reçu l’état marche/arrêt de la cible. Les conditions, temporisations, cibles par pièce, branches `choose`, blocs `parallel`, couleurs et transitions restent du ressort de HA. Pour deux actions indépendantes sur un appui long, on peut créer deux automatisations ayant le même déclencheur.

## Installation

1. Prépare HA avec Matter, un réseau Thread opérationnel, son routeur de bordure, un XIAO ESP32-C6 et un câble USB de données. Le parcours de compilation utilise Linux et Podman rootless.
2. Suis le [guide du firmware](firmware/README.md) pour compiler, provisionner et appairer le XIAO sur la fabric Matter de HA. Le profil fourni utilise l’attestation de développement du SDK.
3. Copie [custom_components/matter_reflex](custom_components/matter_reflex) dans `config/custom_components/` sur HA. Vérifie la configuration, redémarre Core puis ajoute **Matter Reflex** dans **Paramètres → Appareils et services**. Sélectionne le XIAO. Si le formulaire demande la clé de transfert, utilise celle provisionnée sur la carte.
4. Essaie le raccourci ci-dessus. En fonctionnement normal, **Synchronisé** est actif et **Mode reflex actif** est éteint. HA commande la lampe.

## Fonctionnement du relais

- **Quand HA répond**, l’intégration renouvelle le bail toutes les 2,5 secondes sur l’endpoint 2 avec la commande Matter `OnWithTimedOff`. Le XIAO reste abonné aux événements des boutons et ignore les appuis pendant le bail.
- **À l’expiration du bail**, le XIAO utilise ses sessions Matter pour envoyer directement les commandes aux cibles. Au retour de HA, le renouvellement du bail remet la carte en veille. Le compteur d’exécutions inclut les échecs. L’événement `RuleExecuted` porte le statut individuel.
- **À la synchronisation**, HA compile les règles, accorde les droits nécessaires sur les endpoints et vérifie l’empreinte enregistrée par le XIAO en mémoire non volatile. Le transfert passe par le cluster de développement `0xFFF1FC00` sur l’endpoint 1 ou, si le serveur ne le prend pas en charge, par HTTP sur IPv6 Thread (port 5581), authentifié par HMAC-SHA256 avec défi à usage unique. Les commandes aux appareils passent par Matter dans les deux cas. Évite de modifier l’ACL du même appareil pendant sa synchronisation.

## Essais sur matériel

Le XIAO ESP32-C6 a été testé avec le firmware 1.1.0, l’intégration 0.3.0, Home Assistant Core 2026.11.1 et Matter Server 9.3.0.

| Essai | Observation |
| --- | --- |
| BILRESA → KAJPLATS | Double appui et allumage à 70 % observés avec HA disponible, puis avec le serveur Matter arrêté. |
| Six gestes d’une BILRESA | Serveur arrêté et intégration Matter suspendue, les six gestes ont commandé la prise GRILLPLATS, les deux lumières et le store Zemismart, y compris l’ouverture à 75 % et la fermeture. Neuf résultats ont été comptés pendant la séquence. Un dixième a été relevé à la reprise, sans attribution certaine. |
| Redémarrage du XIAO et retour de HA | Rattachement à Thread, restauration de la table de règles, puis retour en veille après renouvellement du bail. |

Pour reproduire un essai de panne, relève le compteur par USB et vérifie `active=1` avant l’appui. HA peut relancer automatiquement son serveur Matter. Utilise une cible observable et vérifie le retour à `active=0` après la reprise. La latence précise et le comportement après la perte simultanée du routeur de bordure et des sessions Matter restent à mesurer.

## Développement

Depuis la racine du dépôt, les commandes suivantes utilisent les conteneurs du projet.

```sh
podman --cgroup-manager=cgroupfs build -f tools/Containerfile.ha -t localhost/matter-reflex-ha:2026.11.1 tools
./dev ha python -m pytest -q tests
./dev ha ruff check custom_components/matter_reflex
./dev idf bash -ec 'g++ -std=c++17 -Wall -Wextra -Werror firmware/main/reflex_engine.cpp tests/engine_test.cpp -o /tmp/engine && /tmp/engine'
./dev matter idf.py -C firmware build
```

Le [compilateur HA](custom_components/matter_reflex/compiler.py) définit le sous-ensemble d’automatisations pris en charge. Le [moteur embarqué](firmware/main/reflex_engine.cpp) décode les règles. Les [tests](tests/) vérifient notamment le format partagé, les autorisations Matter et le comportement du moteur.
