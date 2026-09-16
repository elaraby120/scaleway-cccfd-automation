# Moxe — démarrage et arrêt automatiques Scaleway

Le contrôleur `scheduler/controller.py` s'exécute dans **Scaleway Serverless Jobs**,
indépendamment de GitHub Actions. GitHub sert au déploiement et aux commandes manuelles.

## Horaires et contrôles

Tous les horaires sont en **Europe/Paris**, changement été/hiver compris.

- Lundi–samedi : démarrage anticipé à **08:50**, arrêt à **21:00**.
- Réconciliation toutes les **5 minutes entre 08:00 et 21:55**, lundi–samedi.
  Avant 08:50 et dès 21:00, l'état attendu est `stopped`.
- Arrêt de sécurité chaque jour à minuit, et le dimanche à 09:00 et 21:00.
- La tâche lit l'état avant toute action. Elle n'envoie pas de commande supplémentaire
  pendant une transition `starting`/`stopping` et attend l'état final après l'appel API.
- Lorsque la machine doit fonctionner, elle vérifie la page de connexion Moxe et
  `https://flow.moxe.ma/healthz`. Une application indisponible provoque un échec du job,
  jamais un redémarrage forcé de la machine.
- Délai de convergence : 180 secondes ; limite d'exécution du job : 240 secondes ;
  une nouvelle tentative Scaleway en cas d'échec, puis les contrôles suivants.

`running` indique que la VM fonctionne. Le contrôle de connexion HTTP et de santé n8n
confirme leur disponibilité, sans exécuter de transaction métier.

## Ressources et secrets

La configuration déclarative se trouve dans `scheduler/config.json` : région `fr-par`,
**560 mvCPU, 1 024 Mio de RAM**, 1 024 Mio de stockage temporaire.
L'image officielle Python est fixée par son digest `linux/amd64`.

Le script est monté à `/controller.py` depuis Secret Manager ; la clé API est injectée
par une référence secrète `SCALEWAY_TOKEN`. Elle n'est ni publiée dans le dépôt, ni
exposée dans les variables ordinaires du job, ni téléchargée sur le poste local.
Le déploiement réutilise les secrets GitHub `SCALEWAY_TOKEN` et `INSTANCE_ID`.
Une empreinte du contenu identifie chaque version et permet le redéploiement sans
nouvelle version inutile. Une rotation du token GitHub crée une nouvelle référence
de version au prochain déploiement. Les anciennes versions sont conservées pour
permettre un retour arrière et restent facturées tant qu'elles ne sont pas supprimées.

## Déploiement et bascule

Workflow GitHub : **Deploy Scaleway Serverless Scheduler**.

1. `inspect` : contrôle l'identité de la machine et les accès aux API Jobs/Secret Manager.
2. `stage` : crée/met à jour la tâche sans programmation et avec `MODE=status`.
   Lance et attend une exécution réelle en lecture seule. Refuse de modifier un
   ordonnanceur qui possède déjà des déclencheurs actifs.
3. `activate` : lance un contrôle en lecture seule, puis une réconciliation réelle.
   Installe les trois déclencheurs uniquement après leur succès.
4. `status` : affiche les déclencheurs et les huit dernières exécutions Scaleway.
5. Après une première exécution **programmée** réussie, retirer les anciens cron GitHub
   et conserver le workflow de commande manuelle.

Toutes les commandes utilisent la définition de job portant exactement le nom
`moxe-instance-scheduler`, dans le projet de l'instance. Une identité différente ou
des doublons provoquent un arrêt du déploiement.

Exemple depuis une session GitHub autorisée :

```sh
gh workflow run deploy-scaleway-scheduler.yml -f operation=status
```

Pour mettre à jour le code : lancer `deactivate`, puis `stage`, puis `activate`.
Prévoir cette courte pause hors de l'heure de démarrage/arrêt. `deactivate` retire
les déclencheurs de cette tâche et repasse en lecture seule, sans modifier l'état
de la machine. En cas d'échec de bascule, les commandes manuelles GitHub restent
disponibles pour démarrer/arrêter la machine. Une programmation de secours doit
toujours être réactivée explicitement ; le déploiement ne le fait pas automatiquement.
Une commande manuelle ne suspend pas le calendrier : le prochain passage rétablit
l'état correspondant à l'heure. Utiliser `deactivate` pour une dérogation prolongée,
puis `activate` après intervention.

## Alertes

Le job retourne un échec en cas d'erreur API, de transition qui n'aboutit pas, ou
d'application indisponible. Dans Cockpit → Alerts, la règle Serverless Jobs
**Job Run failed** est activée depuis le 16 septembre 2026. **Le destinataire
e-mail reste à renseigner** avec l'adresse choisie par le propriétaire.
Les notifications nécessitent un contact confirmé dans le gestionnaire d'alertes.
Cette règle détecte les exécutions en échec ; elle ne détecte pas l'absence totale
de déclenchement du service de planification.

## Coût

Environ **4 400 exécutions mensuelles** pour 26 jours ouvrés, plus les essais et reprises.
Le coût réel dépend de la durée mesurée et des quotas encore disponibles sur le compte.
Avec 2 secondes par contrôle, la consommation estimée est de 4 928 vCPU-s et
8 800 Go-s par mois, sous les quotas gratuits publiés de 200 000 vCPU-s / 400 000 Go-s.
Hors quota gratuit, cette consommation représente environ **0,067 € HT/mois**.

- Secret Manager : **0,04 € par version conservée par mois**, plus **0,03 € pour
  10 000 lectures**. Deux références sont lues à chaque exécution.
- Alerte Cockpit : **0,015 € HT/jour par règle active**, soit **0,45 € pour 30 jours**.
- Logs/métriques Scaleway : inclus avec la rétention standard.
- Machine, volumes, IP et services annexes : facturés séparément.

Au déploiement initial, trois versions de secrets sont conservées (dont une
ancienne version de référence de clé) : estimation de **0,60 € HT/mois** pour
secrets et alerte, hors calcul couvert par le quota gratuit. À 2 secondes par
contrôle sans quota disponible, le total est d'environ **0,67 € HT/mois**.
Ces montants sont des estimations, pas un plafond de facturation. L'anticipation
du démarrage ajoute également environ 4,3 heures de VM par mois (26 × 10 minutes).

Tarifs vérifiés le 16 septembre 2026 :
[Serverless](https://www.scaleway.com/fr/tarifs/serverless/),
[Secret Manager](https://www.scaleway.com/fr/tarifs/security-and-account/),
[Cockpit](https://www.scaleway.com/en/pricing/managed-services/).

## Validation

```sh
python3 -m unittest discover -s tests -v
```

Les tests couvrent les horaires été/hiver, le dimanche, les transitions, la
confirmation après démarrage, les échecs de santé, le mode lecture seule, l'absence
de secrets dans les variables ordinaires, et la rotation des versions de clé API.
La validation finale exige en plus une exécution réelle, puis une exécution
programmée réussie dans Scaleway.

### Bascule du 16 septembre 2026

- Job : `ce29c8af-5c7e-4b9f-84cf-a36a7cce53f6`, projet Moxe, région `fr-par`.
- [Activation validée](https://github.com/elaraby120/scaleway-cccfd-automation/actions/runs/35117498784) :
  contrôle en lecture seule, puis réconciliation, tous deux réussis en 2 secondes.
- Premier passage programmé : `44799bc7-5c1f-4664-98d5-5f01e9ce2530`,
  créneau 17:50 Europe/Paris, statut `succeeded`, durée 2 secondes.
  Journal à `2026-09-16T15:50:21Z` : `state=running`, `mode=reconcile`,
  `healthy=true`, aucune commande de démarrage/arrêt nécessaire.
- Les anciens cron GitHub et le keep-alive mensuel sont retirés après cette preuve.
- Un essai préalable a subi `interrupted / technical_error` côté Scaleway ; la
  relance puis l'exécution programmée ont réussi. La planification reste dépendante
  du fournisseur et les erreurs doivent être surveillées.
- La machine était déjà démarrée pendant la bascule. Le cycle réel arrêt du soir /
  démarrage du lendemain n'a pas été forcé pendant les heures de travail.
- La règle d'alerte est active ; les notifications restent en attente du destinataire.
