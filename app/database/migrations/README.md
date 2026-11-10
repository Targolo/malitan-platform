# Database Migrations

Ce dossier contient les fichiers de migration de base de données pour Malitan 3D Manager.

## 📁 Structure

Chaque fichier de migration suit la convention de nommage :
```
migration_<version_from>_to_<version_to>.sql
```

Exemple : `migration_1.0.0_to_1.0.1.sql`

### 🏷️ États des Fichiers

- **`.sql`** : Migration **non appliquée** en production
- **`.sql.done`** : Migration **déjà appliquée** en production (renommé automatiquement par le script)

Les fichiers `.sql.done` sont conservés pour l'historique et la documentation.

**Note :** Pour marquer manuellement une migration comme appliquée (si elle a été exécutée avant l'implémentation de cette fonctionnalité), renommez simplement le fichier :
```bash
mv database/migrations/migration_X_to_Y.sql database/migrations/migration_X_to_Y.sql.done
```

## 🚀 Utilisation

### Appliquer une Migration en Production

**⚠️ IMPORTANT : Le fichier de migration est créé automatiquement lors des changements de version**

Quand vous modifiez `database/models.py`, le fichier SQL de migration correspondant est créé dans ce dossier avec les instructions SQL nécessaires.

**Pour appliquer la migration :**

```bash
python scripts/apply_migration.py database/migrations/migration_1.0.0_to_1.0.1.sql
```

Le script :
- 📄 Lit automatiquement les credentials depuis `.env.production`
- 📋 Affiche le SQL qui sera exécuté
- ⚠️ Demande confirmation
- 🚀 Applique la migration dans une transaction
- ✅ Confirme le succès ou fait un rollback si erreur
- 📝 **Renomme automatiquement le fichier de `.sql` en `.sql.done`** pour marquer qu'elle a été appliquée

## 📋 Workflow Complet

### En Développement

1. Modifier le modèle dans `database/models.py`
2. Le fichier SQL de migration est créé automatiquement dans `database/migrations/`
3. Vérifier le contenu du fichier SQL
4. Commiter le tout ensemble

### En Production

1. Pusher le code sur GitHub :
   ```bash
   git push origin main
   ```

2. **IMMÉDIATEMENT APRÈS**, appliquer la migration (avant que Streamlit Cloud redémarre) :
   ```bash
   python scripts/apply_migration.py database/migrations/migration_1.0.1_to_1.0.2.sql
   ```

3. ✅ Terminé ! Streamlit Cloud redémarre automatiquement avec la nouvelle version

## 🔒 Sécurité

- ✅ Le script demande TOUJOURS confirmation avant d'exécuter
- ✅ Affiche le SQL qui sera exécuté
- ✅ Utilise des transactions (rollback automatique en cas d'erreur)
- ✅ Lit automatiquement les credentials depuis `.env.production`
- ⚠️ Ne JAMAIS modifier une migration déjà appliquée en production
- ⚠️ Toujours créer une NOUVELLE migration pour corriger une erreur

## 📝 Bonnes Pratiques

1. **Une migration = une version**
   - Regrouper tous les changements d'une version dans un fichier

2. **Migrations idempotentes** (si possible)
   - Utiliser `IF NOT EXISTS`, `IF EXISTS`, etc.
   - Permet de ré-exécuter sans erreur si besoin

3. **Données de production**
   - Toujours inclure des UPDATE pour les données existantes
   - Ne pas oublier les valeurs par défaut

4. **Tests de vérification**
   - Ajouter des SELECT à la fin pour vérifier le résultat

5. **Commentaires**
   - Bien documenter ce que fait chaque étape

6. **Fichiers .sql.done**
   - Les fichiers `.sql.done` sont **versionnés dans Git** (historique complet)
   - Ne pas supprimer les fichiers `.sql.done` (documentation importante)

## 🗂️ Historique des Migrations

- `migration_1.0.0_to_1.0.1.sql` - Rendre le type de filament obligatoire (2025-11-08)
- `migration_1.0.1_to_1.0.2.sql` - Ajout du champ adresse_livraison dans la table orders (2025-11-08)
- `migration_1.0.2_to_1.0.3.sql` - Ajout des types de produits (partage_benefices / revente_lea) et du champ prix_vente_lea (2025-11-08)
- `migration_1.0.3_to_1.0.4.sql` - Gestion du stock de produits finis pré-imprimés (finished_goods_stock et finished_goods_movements) (2025-11-08)
- `migration_1.0.4_to_1.0.5.sql` - Ajout du rôle utilisateur 'danielle' (2025-01-XX)
- `migration_1.0.5_to_1.0.6.sql` - Ajout du champ invoice_path pour stocker les chemins des factures PDF (2025-01-XX)
- `migration_1.0.6_to_1.1.0.sql` - Ajout de la fonctionnalité de personnalisation : champ 'personalisable' (boolean) sur products et 'personnalisation' (text) sur order_lines (2025-01-XX)
- `migration_1.1.0_to_1.2.0.sql` - Ajout de la gestion de stock pour les produits finis : champs 'gestion_stock' (boolean) et 'alerte_stock' (integer) sur products (2025-01-XX)
- `migration_1.2.0_to_1.2.1.sql` - Ajout du champ 'nom_original' dans la table colors pour séparer le nom indicatif du nom original du fabricant (2025-01-XX)
- `migration_1.2.1_to_1.2.2.sql` - Ajout du champ 'adresse' unifié dans la table clients pour remplacer adresse_fr et adresse_he (2025-01-XX)
- `migration_1.2.2_to_1.3.0.sql` - Ajout du flag ne_pas_emballer, du timestamp date_pret_livraison et du statut pret_livraison (2025-11-17)
- `migration_1.3.0_to_2.0.1.sql` - Activation de la sécurité RLS (Row Level Security) sur toutes les tables pour sécuriser l'API PostgREST (2025-11-24)
- `migration_2.2.0_to_2.3.0.sql` - Simplification du système de reversements : passage à un système de balance/solde. Suppression des champs reverse/date_reversement/reversement_id dans order_lines. Les reversements deviennent un simple journal de paiements indépendant des lignes de commande. (2025-12-16)

## 🆘 En Cas de Problème

Si une migration échoue :

1. Le script fait automatiquement un ROLLBACK
2. Vérifiez les logs d'erreur
3. Corrigez le fichier SQL
4. Testez localement si possible
5. Ré-exécutez

Si vous avez besoin d'annuler une migration déjà appliquée :
- Créez une **migration de rollback** (ex: `migration_1.0.1_to_1.0.0_rollback.sql`)
- Incluez les instructions inverses (DROP COLUMN, etc.)
