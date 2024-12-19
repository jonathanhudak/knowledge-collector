# Makefile

.PHONY: service cli

service:
	FLASK_ENV=development FLASK_APP=transcript_service.py flask run --reload

cli:
	python transcript_service.py
