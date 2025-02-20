# dep-npm-grabber
Look for npm dependencies in js / package.json file and check potential takeover 

# TL;DR
## Collect
1. Retreive list of urls grabbed by dependencies": string in body
- `cat webservers.txt | katana -jc -d 2 -o katana.txt`
- `cat katana.txt | anew | httpx -silent -ms 'dependencies":' | anew scope.txt `
2. Retreive 3rdpartylicenses files
- `cat webservers.txt|httpx -path "/3rdpartylicenses.txt" -ms "Apache License" | anew scope.txt `
3. Retreive package.json, package-lock.json, yarn.lock files
- `cat webservers.txt | nuclei -id yarn-lock,package-json -silent | awk '{print $4}' | anew scope.txt`

## Exploit
3. Launch dep-npm-grabber
- `python3 dep-npm-grabber.py -f scope.txt -v`
4. Verify takeover
- `python3 dep-npm-grabber.py --check-takeover`

# Help
```
$ python3 dep-grabber.py --help
usage: dep-grabber.py [-h] [-u URLS [URLS ...] | -f FILE] [-v] [-d] [-ct]

Dependency extractor from URLs

optional arguments:
  -h, --help            show this help message and exit
  -u URLS [URLS ...], --urls URLS [URLS ...]
                        List of URLs to analyze
  -f FILE, --file FILE  File containing URLs (one per line)
  -v, --verbose         Verbosity level (-v or -vv)
  -d, --display         Display the results table
  -ct, --check-takeover
                        Check for package takeover possibilities
```
