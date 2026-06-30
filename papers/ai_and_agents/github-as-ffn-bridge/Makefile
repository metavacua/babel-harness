SRCS      := $(wildcard src/0*.xml)
HTML_OUTS := $(patsubst src/%.xml,generated/%.html,$(SRCS))
TEX_OUTS  := $(patsubst src/%.xml,generated/%.tex,$(SRCS))

.PHONY: all html latex validate clean

all: validate html latex

validate: $(SRCS)
	@for f in $(SRCS); do \
	  echo "Validating $$f ..."; \
	  xmllint --noout --xinclude \
	    --relaxng https://docbook.org/xml/5.2/rng/docbook.rng "$$f" && \
	  echo "  OK"; \
	done

html: $(HTML_OUTS)
generated/%.html: src/%.xml xsl/html5.xsl | generated
	xsltproc --xinclude xsl/html5.xsl $< > $@

latex: $(TEX_OUTS)
generated/%.tex: src/%.xml xsl/latex.xsl | generated
	xsltproc --xinclude xsl/latex.xsl $< > $@

pdf: latex
	@for f in $(TEX_OUTS); do \
	  cd generated && pdflatex -interaction=nonstopmode "$$(basename $$f)" && cd ..; \
	done

generated:
	mkdir -p generated

clean:
	rm -f generated/*.html generated/*.tex generated/*.pdf
	rm -f generated/*.aux generated/*.log generated/*.out generated/*.toc
