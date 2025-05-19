VENV := .venv
MKDOCS := $(VENV)/bin/mkdocs

# List of files to copy into ./docs
DOC_FILES := README.md CONTRIBUTING.md

.PHONY: all docs clean

all: docs

docs:
	mkdir -p docs
	@for file in $(DOC_FILES); do \
		if [ -f $$file ]; then \
			cp $$file docs/; \
			echo "Copied $$file to docs/"; \
		else \
			echo "Warning: $$file not found."; \
		fi \
	done
	$(MKDOCS) serve

clean:
	rm -rf $(VENV)
	@for file in $(DOC_FILES); do \
		rm -f docs/$$file; \
	done
