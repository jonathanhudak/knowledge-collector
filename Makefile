# Makefile

.PHONY: service

service:
	FLASK_ENV=development FLASK_APP=transcript_service.py flask run --reload
