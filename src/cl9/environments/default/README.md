# {{PROJECT_NAME}}

*Please write a brief description of what this project is about.*

## Project Structure

This project uses the cl9 default environment type (Nix + direnv).

| Directory | Purpose |
|-----------|---------|
| `src/` | Source code checkouts. Policy: deliberate copies, same repo can exist in multiple projects. |
| `doc/` | Human-readable documents. Google Docs exports, design notes, etc. |
| `data/` | Data artifacts. CSVs, SQLite databases, downloaded files. |

## Environment

This project depends on:
- **Nix** (with flakes enabled) - for reproducible tool management
- **direnv** - for automatic environment loading

### Adding Tools

Edit `flake.nix` to add tools. Search available packages:

```bash
nix search nixpkgs <tool>
```

Over 100,000 packages available.

### Environment Variables

Add variables to `.envrc`. Secrets can go here.

### Entering the Project

```bash
cd {{PROJECT_PATH}}
direnv allow    # First time only
```

Or use cl9:

```bash
cl9 enter {{PROJECT_NAME}}
```
