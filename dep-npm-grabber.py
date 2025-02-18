import sys
import json
import sqlite3
import requests
import re
from rich.console import Console
from rich.table import Table
from rich.progress import track
from rich import print as rprint
from typing import List, Dict
from urllib.parse import urlparse
from R2Log import logger, R2Log
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
import argparse
from pathlib import Path
from rich.live import Live
from rich.console import Group
import time
import urllib3

# Désactive les avertissements SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initialisation de la console pour R2Log
console = Console()
R2Log.console = console

def create_database():
    """Crée la base de données SQLite pour stocker les dépendances"""
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS dependencies
                 (url text, name text, version text, type text)''')
    
    conn.commit()
    conn.close()
    logger.verbose("Base de données créée avec succès")

def parse_package_json(content: str) -> Dict[str, List[Dict[str, str]]]:
    """Parse le contenu d'un package.json"""
    try:
        data = json.loads(content)
        dependencies = []
        
        if "dependencies" in data:
            for name, version in data["dependencies"].items():
                dependencies.append({
                    "name": name,
                    "version": version,
                    "type": "dependency"
                })
                logger.advanced(f"Dépendance trouvée: {name}@{version}")
                
        if "devDependencies" in data:
            for name, version in data["devDependencies"].items():
                dependencies.append({
                    "name": name, 
                    "version": version,
                    "type": "devDependency"
                })
                logger.advanced(f"Dépendance de dev trouvée: {name}@{version}")
                
        return {"dependencies": dependencies}
    
    except json.JSONDecodeError:
        logger.error("Erreur: JSON invalide")
        return {"dependencies": []}

def parse_js_dependencies(content: str) -> Dict[str, List[Dict[str, str]]]:
    """Parse les dépendances depuis un fichier JS"""
    dependencies = []
    
    # Modifie les patterns pour capturer tout le contenu jusqu'à la prochaine accolade fermante
    dep_pattern = r'dependencies:\{([^}]+)\}'
    dev_dep_pattern = r'devDependencies:\{([^}]+)\}'
    
    def parse_dep_string(dep_str: str, dep_type: str):
        """Parse une chaîne de dépendances et vérifie les virgules restantes"""
        # Utilise une expression régulière plus robuste pour extraire les paires nom:version
        pairs = re.finditer(r'(?:"|,)?(["\w\-\.]+):"([^"]+)"', dep_str)
        
        for match in pairs:
            name, version = match.groups()
            # Nettoie le nom (enlève les guillemets si présents)
            name = name.strip('"')
            # Vérifie que le nom et la version sont cohérents
            if name and version:
                dependencies.append({
                    "name": name,
                    "version": version,
                    "type": dep_type
                })
                logger.advanced(f"Dépendance {dep_type} trouvée: {name}@{version}")
            else:
                logger.debug(f"Partie ignorée car format invalide: {name}:{version}")
    
    # Parse les dépendances normales
    for dep_match in re.finditer(dep_pattern, content):
        deps = dep_match.group(1)
        parse_dep_string(deps, "dependency")
            
    # Parse les dépendances de dev
    for dev_match in re.finditer(dev_dep_pattern, content):
        dev_deps = dev_match.group(1)
        parse_dep_string(dev_deps, "devDependency")
            
    return {"dependencies": dependencies}

def save_dependencies(url: str, dependencies: List[Dict[str, str]]):
    """Sauvegarde les dépendances dans la base de données"""
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    new_deps = 0
    for dep in dependencies:
        # Vérifie si la dépendance existe déjà avec la même version
        c.execute("""
            SELECT * FROM dependencies 
            WHERE name = ? AND version = ?
        """, (dep["name"], dep["version"]))
        
        if not c.fetchone():
            c.execute("INSERT INTO dependencies VALUES (?,?,?,?)", 
                     (url, dep["name"], dep["version"], dep["type"]))
            new_deps += 1
    
    conn.commit()
    conn.close()
    if new_deps > 0:
        logger.success(f"{new_deps} nouvelles dépendances sauvegardées pour {url}")
    else:
        logger.advanced(f"Aucune nouvelle dépendance pour {url}")
    return new_deps

def load_urls_from_file(file_path: str) -> List[str]:
    """Charge les URLs depuis un fichier"""
    try:
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        logger.success(f"Chargement de {len(urls)} URLs depuis {file_path}")
        return urls
    except Exception as e:
        logger.error(f"Erreur lors de la lecture du fichier {file_path}: {str(e)}")
        return []

def process_urls(urls: List[str]):
    """Traite une liste d'URLs avec des barres de progression"""
    total_deps = 0
    
    # Création de la barre de progression principale
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        expand=True,
        console=Console(force_terminal=True),
        transient=True
    ) as progress:
        
        main_task = progress.add_task("[cyan]Analyse en cours [0/{0}]".format(len(urls)), total=len(urls))
        
        for index, url in enumerate(urls, start=1):
            try:
                # Désactive la vérification SSL
                response = requests.get(url, verify=False)
                response.raise_for_status()
                content = response.text
                
                path = urlparse(url).path
                if path.endswith('package.json'):
                    deps = parse_package_json(content)
                else:
                    deps = parse_js_dependencies(content)
                
                if deps["dependencies"]:
                    new_deps = save_dependencies(url, deps["dependencies"])
                    total_deps += new_deps
                    
                    if logger.getEffectiveLevel() < 13:
                        for dep in deps["dependencies"]:
                            progress.console.print(f"[blue]  → {dep['name']}@{dep['version']}")
                    
                    if logger.getEffectiveLevel() <= 15:
                        progress.console.print(f"[green]✓ {url} ({new_deps} nouvelles dépendances)")
                else:
                    if logger.getEffectiveLevel() <= 15:
                        progress.console.print(f"[yellow]⚠ {url} (aucune dépendance)")
            
            except requests.exceptions.SSLError as e:
                if logger.getEffectiveLevel() <= 15:
                    progress.console.print(f"[red]✗ {url} (erreur SSL: certificat non valide)")
            except Exception as e:
                if logger.getEffectiveLevel() <= 15:
                    progress.console.print(f"[red]✗ {url} (erreur: {str(e)})")
            
            finally:
                progress.update(main_task, advance=1)
                progress.update(main_task, description=f"[cyan]Analyse en cours [{index}/{len(urls)}]")
                
    return total_deps

def display_results():
    """Affiche les résultats dans un tableau"""
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("URL")
    table.add_column("Nom")
    table.add_column("Version") 
    table.add_column("Type")
    
    rows = c.execute('SELECT * FROM dependencies').fetchall()
    for row in rows:
        table.add_row(row[0], row[1], row[2], row[3])
        
    logger.raw(table)
    logger.info(f"Total des dépendances trouvées: {len(rows)}")
    conn.close()

def check_package_takeover(package_name: str, progress=None) -> bool:
    """Vérifie si un package est potentiellement vulnérable au takeover"""
    try:
        time.sleep(0.2)
        
        url = f"https://registry.npmjs.org/{package_name}"
        if logger.getEffectiveLevel() < 13:
            progress.console.print(f"[yellow]Vérification du package {package_name}")
        
        # Désactive la vérification SSL également pour les vérifications de takeover
        response = requests.get(url, verify=False)
        
        if logger.getEffectiveLevel() <= 15:
            progress.console.print(f"[blue]Réponse du paquet {package_name}: {response.status_code}")
        
        if response.status_code == 404 or response.text == '{"error":"Not found"}':
            return True
        return False
        
    except Exception as e:
        if progress and progress.console:
            progress.console.print(f"[red]Erreur lors de la vérification de {package_name}: {str(e)}")
        return False

def check_all_takeovers():
    """Vérifie tous les packages pour des takeovers potentiels"""
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    # Récupère tous les noms de packages uniques
    c.execute('SELECT DISTINCT name FROM dependencies')
    packages = [row[0] for row in c.fetchall()]
    logger.info(f"Vérification de {len(packages)} packages uniques")
    
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        expand=True,
        console=Console(force_terminal=True),
        transient=True
    ) as progress:
        
        check_task = progress.add_task(
            "[cyan]Vérification des packages...", 
            total=len(packages)
        )
        
        for i, package in enumerate(packages, start=1):
            if check_package_takeover(package,progress):
                c.execute('SELECT DISTINCT url FROM dependencies WHERE name = ?', (package,))
                urls = [row[0] for row in c.fetchall()]
                
                warning_msg = f"[yellow]⚠ Possible takeover pour le package {package}\n"
                warning_msg += "  URLs affectées:\n"
                for url in urls:
                    warning_msg += f"  - {url}\n"
                progress.console.print(warning_msg)
            
            progress.update(check_task, advance=1, description=f"[cyan]Vérification des packages [{i}/{len(packages)}]")
    
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="Extracteur de dépendances depuis des URLs")
    
    # Groupe pour les options d'entrée (non requis si -ct est utilisé)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument('-u', '--urls', nargs='+', help='Liste d\'URLs à analyser')
    input_group.add_argument('-f', '--file', help='Fichier contenant les URLs (une par ligne)')
    
    parser.add_argument('-v', '--verbose', action='count', default=0, help='Niveau de verbosité (-v ou -vv)')
    parser.add_argument('-d', '--display', action='store_true', help='Affiche le tableau des résultats')
    parser.add_argument('-ct', '--check-takeover', action='store_true', help='Vérifie les possibilités de takeover des packages')
    
    args = parser.parse_args()
    
    # Configuration du niveau de verbosité
    logger.setVerbosity(args.verbose)
    
    if args.check_takeover:
        # Si on demande uniquement la vérification des takeovers
        logger.info("Démarrage de la vérification des takeovers...")
        check_all_takeovers()
        return
        
    # Vérifie qu'une source d'URLs est fournie si -ct n'est pas utilisé
    if not args.urls and not args.file:
        parser.error("Un des arguments -u/--urls ou -f/--file est requis si -ct n'est pas utilisé")
    
    # Sinon, on continue avec le crawl normal
    if args.file:
        urls = load_urls_from_file(args.file)
        if not urls:
            logger.critical("Aucune URL valide trouvée dans le fichier")
            sys.exit(1)
    else:
        urls = args.urls
    
    create_database()
    total_deps = process_urls(urls)
    
    if args.display:
        display_results()
    
    logger.success(f"Analyse terminée: {total_deps} dépendances trouvées dans {len(urls)} URLs")

if __name__ == "__main__":
    main()
