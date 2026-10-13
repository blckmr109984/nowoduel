#!/data/data/com.termux/files/usr/bin/bash
cd ~/bot1 || exit 1
mkdir -p backups
cp game.db "backups/game-$(date +%F-%H%M).db" 2>/dev/null
git pull
pip install -r requirements.txt -q
if tmux has-session -t bot 2>/dev/null; then
  tmux kill-session -t bot
  tmux new -d -s bot 'cd ~/bot1 && python bot.py'
  echo "Бот обновлён и перезапущен"
else
  echo "Бот сейчас выключен по расписанию, новая версия подхватится при запуске"
fi