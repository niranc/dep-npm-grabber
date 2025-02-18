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
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()
R2Log.console = console

def create_database():
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS dependencies
                 (url text, name text, version text, type text)''')
    
    conn.commit()
    conn.close()
    logger.verbose("Database created successfully")

def parse_package_json(content: str) -> Dict[str, List[Dict[str, str]]]:
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
                logger.advanced(f"Dependency found: {name}@{version}")
                
        if "devDependencies" in data:
            for name, version in data["devDependencies"].items():
                dependencies.append({
                    "name": name, 
                    "version": version,
                    "type": "devDependency"
                })
                logger.advanced(f"Dev dependency found: {name}@{version}")
                
        return {"dependencies": dependencies}
    
    except json.JSONDecodeError:
        logger.error("Error: Invalid JSON")
        return {"dependencies": []}

def parse_js_dependencies(content: str) -> Dict[str, List[Dict[str, str]]]:
    logger.advanced("Looking for dependencies")
    dependencies = []
    
    dep_pattern = r'"dependencies"\s*:\s*\{([^}]*)\}'
    dev_dep_pattern = r'"devDependencies"\s*:\s*\{([^}]*)\}'
    
    def parse_dep_string(dep_str: str, dep_type: str):
        pairs = re.finditer(r'(?:"|,)?(["\w\-\.]+):"([^"]+)"', dep_str)
        pairs = list(re.finditer(r'(?:"|,)?(["\w\-\.]+):"([^"]+)"', dep_str))
        #logger.advanced(f"Pairs found: {pairs}")
        
        for match in pairs:
            name, version = match.groups()
            name = name.strip('"')
            #logger.advanced(f"Parsing dependency: {name}@{version}")
            if name and version:
                dependencies.append({
                    "name": name,
                    "version": version,
                    "type": dep_type
                })
                logger.advanced(f"{dep_type.capitalize()} found: {name}@{version}")
            else:
                logger.advanced(f"Ignored part due to invalid format: {name}:{version}")
    
    for dep_match in re.finditer(dep_pattern, content):
        deps = dep_match.group(1)
        logger.advanced(f"Dep match found: {deps}")
        parse_dep_string(deps, "dependency")
            
    for dev_match in re.finditer(dev_dep_pattern, content):
        dev_deps = dev_match.group(1)
        logger.advanced(f"Dev dep match found: {dev_deps}")
        parse_dep_string(dev_deps, "devDependency")
            
    return {"dependencies": dependencies}

def save_dependencies(url: str, dependencies: List[Dict[str, str]]):
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    new_deps = 0
    for dep in dependencies:
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
        logger.success(f"{new_deps} new dependencies saved for {url}")
    else:
        logger.advanced(f"No new dependencies for {url}")
    return new_deps

def load_urls_from_file(file_path: str) -> List[str]:
    try:
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        logger.success(f"Loaded {len(urls)} URLs from {file_path}")
        return urls
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {str(e)}")
        return []

def process_urls(urls: List[str]):
    total_deps = 0
    
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        expand=True,
        console=Console(force_terminal=True),
        transient=True
    ) as progress:
        
        main_task = progress.add_task("[cyan]Processing [0/{0}]".format(len(urls)), total=len(urls))
        
        for index, url in enumerate(urls, start=1):
            try:
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
                        progress.console.print(f"[green]✓ {url} ({new_deps} new dependencies)")
                else:
                    if logger.getEffectiveLevel() <= 15:
                        progress.console.print(f"[yellow]⚠ {url} (no dependencies)")
            
            except requests.exceptions.SSLError as e:
                if logger.getEffectiveLevel() <= 15:
                    progress.console.print(f"[red]✗ {url} (SSL error: invalid certificate)")
            except Exception as e:
                if logger.getEffectiveLevel() <= 15:
                    progress.console.print(f"[red]✗ {url} (error: {str(e)})")
            
            finally:
                progress.update(main_task, advance=1)
                progress.update(main_task, description=f"[cyan]Processing [{index}/{len(urls)}]")
                
    return total_deps

def display_results():
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("URL")
    table.add_column("Name")
    table.add_column("Version") 
    table.add_column("Type")
    
    rows = c.execute('SELECT * FROM dependencies').fetchall()
    for row in rows:
        table.add_row(row[0], row[1], row[2], row[3])
        
    logger.raw(table)
    logger.info(f"Total dependencies found: {len(rows)}")
    conn.close()

def check_package_takeover(package_name: str, progress=None) -> bool:
    try:
        time.sleep(0.2)
        
        url = f"https://registry.npmjs.org/{package_name}"
        if logger.getEffectiveLevel() < 13:
            progress.console.print(f"[yellow]Checking package {package_name}")
        
        response = requests.get(url, verify=False)
        
        if logger.getEffectiveLevel() <= 15:
            progress.console.print(f"[blue]Package response {package_name}: {response.status_code}")
        
        if response.status_code == 404 or response.text == '{"error":"Not found"}':
            return True
        return False
        
    except Exception as e:
        if progress and progress.console:
            progress.console.print(f"[red]Error checking {package_name}: {str(e)}")
        return False

def check_all_takeovers():
    conn = sqlite3.connect('dependencies.db')
    c = conn.cursor()
    
    c.execute('SELECT DISTINCT name FROM dependencies')
    packages = [row[0] for row in c.fetchall()]
    logger.info(f"Checking {len(packages)} unique packages")
    
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
            "[cyan]Checking packages...", 
            total=len(packages)
        )
        
        for i, package in enumerate(packages, start=1):
            if check_package_takeover(package,progress):
                c.execute('SELECT DISTINCT url FROM dependencies WHERE name = ?', (package,))
                urls = [row[0] for row in c.fetchall()]
                
                warning_msg = f"[yellow]⚠ Possible takeover for package {package}\n"
                warning_msg += "  Affected URLs:\n"
                for url in urls:
                    warning_msg += f"  - {url}\n"
                progress.console.print(warning_msg)
            
            progress.update(check_task, advance=1, description=f"[cyan]Checking packages [{i}/{len(packages)}]")
    
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="Dependency extractor from URLs")
    
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument('-u', '--urls', nargs='+', help='List of URLs to analyze')
    input_group.add_argument('-f', '--file', help='File containing URLs (one per line)')
    
    parser.add_argument('-v', '--verbose', action='count', default=0, help='Verbosity level (-v or -vv)')
    parser.add_argument('-d', '--display', action='store_true', help='Display the results table')
    parser.add_argument('-ct', '--check-takeover', action='store_true', help='Check for package takeover possibilities')
    
    args = parser.parse_args()
    
    logger.setVerbosity(args.verbose)
    
    if args.check_takeover:
        logger.info("Starting takeover check...")
        check_all_takeovers()
        return
        
    if not args.urls and not args.file:
        parser.error("One of the arguments -u/--urls or -f/--file is required if -ct is not used")
    
    if args.file:
        urls = load_urls_from_file(args.file)
        if not urls:
            logger.critical("No valid URLs found in the file")
            sys.exit(1)
    else:
        urls = args.urls
    
    create_database()
    total_deps = process_urls(urls)
    
    if args.display:
        display_results()
    
    logger.success(f"Analysis complete: {total_deps} dependencies found in {len(urls)} URLs")

if __name__ == "__main__":
    main()
