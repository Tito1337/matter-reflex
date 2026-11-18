# Contribuer à Matter Reflex

Le compilateur Python décide si HA peut déléguer une automatisation entière. Le firmware C++ doit ensuite reproduire ses actions avec les informations disponibles pendant une panne. Une modification d’un côté peut donc changer le comportement de l’autre.

## Vérifier une modification

Depuis un clone Linux avec Podman rootless, construis d’abord l’image de test HA. Les tests logiciels fonctionnent sans carte USB, instance Home Assistant ou identifiants Thread.

```sh
podman --cgroup-manager=cgroupfs build -f tools/Containerfile.ha -t localhost/matter-reflex-ha:2026.11.1 tools
./dev ha python -m pytest -q
./dev ha ruff check custom_components/matter_reflex tests tools/board_console.py
```

Pour vérifier le contrat entre les deux composants, utilise Bash. La première commande de la chaîne compile l’exemple du README et trois autres actions avec le code Python réel. La seconde fait décoder et exécuter ces règles au moteur C++ sur la machine de développement. Le test couvre notamment un identifiant Matter sur 64 bits, un pas négatif, un store à 75 % et une bascule avec état observé.

```bash
set -o pipefail
./dev ha python - < tests/contract/encode.py | ./dev idf bash tools/test-engine
```

Pour une modification du firmware ou du SDK, compile aussi l’application destinée au microcontrôleur. L’édition des liens vérifie des API CHIP et des contraintes de taille que le test du moteur sur ordinateur ne couvre pas.

```sh
./dev matter idf.py -C firmware build
./dev matter idf.py -C firmware size
```

Les [contrôles logiciels](.github/workflows/checks.yml) s’exécutent sur les push et pull requests. Le [build ESP32-C6](.github/workflows/firmware.yml) s’exécute lorsque le firmware, son lanceur ou son workflow change. Les deux workflows peuvent être déclenchés manuellement sur GitHub.

## Ajouter une destination Matter

1. Relever le type d’appareil, le cluster, les commandes acceptées et les capacités de l’entité HA. Une même prise peut exposer une sortie de puissance et plusieurs paramètres de configuration.
2. Décrire le résultat attendu quand l’état de la cible est connu, inconnu ou devenu périmé. Par exemple, une bascule a besoin d’un état marche/arrêt récent.
3. Tester l’acceptation d’une configuration compatible et le rejet complet d’une automatisation incompatible. Vérifier les autorisations nécessaires sur chaque endpoint et la compatibilité des images de règles existantes.
4. Si le format ou la sémantique d’une règle change, compléter le [test de contrat](tests/contract) et documenter le comportement à la mise à jour. Dans la pull request, indiquer quels essais ont été effectués sur matériel et avec quelles versions.

## Signaler un problème

Joins les versions de HA Core, de Matter Server, de l’intégration et du firmware. Indique le modèle de la source et de la cible, le geste, le résultat attendu et le résultat observé. Une automatisation minimale avec des entités renommées permet souvent de reproduire le problème. Pour une panne, ajoute le mode Reflex et la variation du compteur relevés pendant l’appui. [Ouvrir un signalement](https://github.com/Tito1337/matter-reflex/issues)

Les dumps de flash, clés HMAC, datasets Thread et codes d’appairage donnent accès à des informations privées. Garde-les hors des signalements et des commits. Remplace également les identifiants d’appareils et les noms domestiques dans les extraits de journaux. Le [guide de dépannage](docs/depannage.md) indique les observations utiles.
