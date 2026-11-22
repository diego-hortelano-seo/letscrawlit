"""What letscrawlit-cli.exe runs: the command line of the Windows program (crawl, ci, schedule, resume, list).

    letscrawlit-cli ci https://staging.misitio.es/ --i-own-this-site --fail-on critical,high --junit seo.xml

A console program, so CI systems get the output and the exit code (0 passed, 1 SEO checks failed, 2 could not run).
"""
import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
