# DBT Training Wheels

SQL to dbt Migration Tool - Convert BigQuery scripts or any type of SQL scripts to dbt models.




## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed on your machine

## Quick Start

**No `.env` files needed!** Just use your SSH keys.

### 1. Check SSH Keys (10 seconds)

```bash
ssh -T git@github.com
```

Should say: `Hi username! You've successfully authenticated...`

**Don't have SSH keys?** Generate one with `ssh-keygen -t ed25519`, then add the public key at <https://github.com/settings/keys>.

### 2. Build and Run with Docker (30 seconds)

```bash
docker-compose up --build
```

The `--build` flag builds the image automatically (first time takes ~1-2 minutes).

The application will start on http://localhost:8000

**Command variations:**
```bash
# With logs visible (recommended for first time)
docker-compose up --build

# Run in background (detached)
docker-compose up --build -d

# Rebuild from scratch (if needed)
docker-compose build --no-cache
docker-compose up
```

### 3. Use the Interface

Open http://localhost:8000 in your browser. Simply upload your SQL files and DBT Training Wheels will:
- Generate dbt models (staging, intermediate, marts)
- Create a pull request in your dbt repository
- **All commits are authored by YOU** (via your SSH keys!)

**Complete setup guide:** See [DOCS.md](./DOCS.md) and [CONTRIBUTING.md](./CONTRIBUTING.md)



### Best Practices

1. **Use descriptive project names**: `analytics` not `ap1`
2. **Include project identifiers in prefixes**: Configure `stg__<project>__`, `mart__<project>__` (not just `stg__`, `mart__`)
3. **Set reasonable default tags**: Include common schedules like `daily`, `weekly`
4. **Document scheduled_query_projects**: List all GCP projects with unmigrated queries
5. **Keep github.base_path organized**: Use `dbt_projects/{project_name}` pattern
6. **Set appropriate schedules**: Use cron syntax (`0 8 * * *` = 8 AM daily)
7. **Define all config per-project**: Don't rely on defaults - each project should be self-contained

## Stopping the App

Press `Ctrl + C` in terminal or run:
```bash
docker-compose down
```

## Troubleshooting

**"Permission denied (publickey)" error**
```bash
# Add your SSH key to GitHub
cat ~/.ssh/id_ed25519.pub  # or id_rsa.pub
# Copy and add at: https://github.com/settings/keys
```

**Container exits immediately**
```bash
# Check logs
docker-compose logs

# Usually means SSH keys aren't set up or config file is missing
```

**GitHub push fails**
- Verify SSH keys are working: `ssh -T git@github.com`
- Check you have write access to the target repository
- Confirm your key is registered at <https://github.com/settings/keys>
