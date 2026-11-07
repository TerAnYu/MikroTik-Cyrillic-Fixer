# MikroTik Cyrillic Fixer

**Версия:** 0019  
**Авторы:** Qwen3.7 & TerAnYu  
**Лицензия:** MIT

## Описание

MikroTik Cyrillic Fixer — это десктопное приложение с графическим интерфейсом для автоматического поиска и исправления проблем с кодировкой кириллицы в конфигурации маршрутизаторов MikroTik RouterOS.

Инструмент решает проблему отображения «кракозябр» (CP1251/Windows-1251), которые возникают при переносе старых конфигураций, использовании WinBox с некорректной локалью или после обновления прошивки. Скрипт безопасно подключается к устройству, находит все поля с битой кодировкой и предлагает исправить их на корректный UTF-8.

## Возможности

- 🔍 **Глубокое сканирование:** Проверка 35+ разделов RouterOS (firewall, scripts, users, interfaces, routing, dhcp, dns и др.)
- 🔐 **Безопасное подключение:** Поддержка API (порт 8728) и API-SSL (порт 8729) с игнорированием самоподписанных сертификатов
- 🛡️ **Защита данных:** 
  - Поле `source` в скриптах не изменяется через API во избежание поломки кода; вместо этого предлагается скопировать декодированный вариант для ручной вставки
  - Read-only и системные поля автоматически исключаются из команд обновления
- ✅ **Пакетная обработка:** Выбор нескольких записей через чекбоксы, Shift/Ctrl-выделение, кнопка «Выбрать все»
- 📋 **Умное копирование:** При выборе скриптов копируется только чистый декодированный код без лишних пустых строк и метаданных
- 🔄 **Стабильность:** Буферизация сокета, сброс буфера между запросами, автопереподключение при обрыве связи, защита от рассинхронизации протокола
- 📊 **Сортировка:** По всем колонкам (раздел, исходный текст, декодированный, статус чекбокса) с индикацией направления

## Требования

- Python 3.8+
- Tkinter (входит в стандартную поставку Python)
- Доступ к MikroTik RouterOS по API или API-SSL
- Учётная запись с правами `write`

## Установка и запуск

```bash
# Клонирование или скачивание скрипта
git clone https://github.com/TerAnYu/MikroTik-Cyrillic-Fixer.git
cd MikroTik-Cyrillic-Fixer

# Запуск (зависимостей кроме Python нет)
python gui_convert.py
```

## Использование

1. Введите IP-адрес, порт, логин и пароль маршрутизатора
2. При необходимости включите «API-SSL» (порт автоматически переключится на 8729)
3. Нажмите **«Подключиться»** → дождитесь зелёного индикатора
4. Нажмите **«Сканировать»** → дождитесь заполнения списка
5. Отметьте чекбоксами нужные записи (или используйте «Выбрать все»)
6. Нажмите **«✔ Применить выбранные»** для автоматического исправления
7. Для скриптов: скопируйте декодированный код кнопкой «📋 Копировать» и вставьте вручную через WinBox/SSH

## Технические детали

### Протокол API
Приложение использует собственный легковесный клиент MikroTik API без сторонних библиотек:
- Корректная обработка challenge-response аутентификации
- Поддержка plain-login для ROSv6.43+/v7.x
- Атомарная отправка предложений с TCP_NODELAY
- Буферизация чтения с защитой от desync

### Определение кодировки
Двухэтапное обнаружение:
1. Экранированные последовательности `\XX\XX` → декодирование из CP1251
2. Сырые байты CP1251 в потоке latin-1 → проверка на валидный UTF-8 → декодирование из CP1251 только если это не уже корректный UTF-8

Это предотвращает двойную конвертацию при повторном сканировании.

## Ограничения

- Поле `source` в `/system/script` **не обновляется автоматически** через API из-за невозможности безопасного экранирования многострочного кода. Используйте кнопку копирования и ручную вставку.
- Некоторые разделы могут отсутствовать на отдельных моделях/версиях ROS — они пропускаются автоматически.
- SSL-подключение требует включённого сервиса `api-ssl` и назначенного сертификата на маршрутизаторе.

## Лицензия

MIT License

Copyright (c) 2026 Qwen3.7 & TerAnYu

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
