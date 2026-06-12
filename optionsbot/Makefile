.PHONY: install test paper backtest halt seed status

install:
	pip install -r requirements.txt

test:
	python -m pytest tests/ -q

# Run the bot in paper mode under the crash-restarting supervisor
paper:
	python -m bot.scheduler.supervisor

# Run the backtest against stored data (seeds sample data if empty)
backtest:
	python -m bot.cli backtest

# EMERGENCY: flatten all positions and halt the bot
halt:
	python -m bot.cli halt

seed:
	python -m bot.cli seed-data

status:
	python -m bot.cli status
