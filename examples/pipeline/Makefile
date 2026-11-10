# Each stage writes the ID of the version it published; the next stage reads exactly that version.
PYTHON ?= python

.DELETE_ON_ERROR:

all: build/evaluate.id

build/points.id: prepare.py
	mkdir -p build
	$(PYTHON) prepare.py > $@

build/fit.id: fit.py build/points.id
	$(PYTHON) fit.py $$(cat build/points.id) > $@

build/evaluate.id: evaluate.py build/fit.id build/points.id
	$(PYTHON) evaluate.py $$(cat build/fit.id) $$(cat build/points.id) > $@
