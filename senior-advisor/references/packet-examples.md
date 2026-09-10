# Worked packet distillations

Distillation keeps the question and every fact that can change its answer, then removes the task, requester, project identity, and session history. Put the result in the six parsed headers; keep measurements and verbatim errors, and reduce evidence to the smallest case that demonstrates the issue. The scenarios and measurements below are worked examples, not benchmarks run against this repository.

## 1. Architectural choice

Before:

```text
I'm working on the storage layout for HarborLedger, and the user asked for a sharding plan.
Earlier I sketched a tenant-hash layout; then we discussed monthly partitions.
We use PostgreSQL 16.2 on Linux 6.8 x86_64. The event table is 1.8 TB across 2,000 tenants.
95% of reads filter by tenant_id and a timestamp range; one tenant generates 32% of writes.
Retention is 90 days, expired data must be removed within 10 minutes, and cross-tenant reads are unnecessary.
Deleting one expired day with DELETE took 47 minutes in the staging dataset.
I expect you'll prefer sharding, but should this table use time-range partitions or tenant-hash partitions?
```

After:

```text
Question: Should the event table use time-range partitions or tenant-hash partitions?
Stack: PostgreSQL 16.2; Linux 6.8 x86_64
Given: 1.8 TB; 2,000 tenants; 95% of reads filter tenant_id and a timestamp range; one tenant produces 32% of writes.
Constraints: Retention 90 days; remove expired data within 10 minutes; cross-tenant reads unnecessary.
Ruled out: DELETE of one expired day on the staging dataset → 47 minutes.
Evidence: none
```

Cut the project name, requester, design-session sequence, and predicted answer because they do not determine the partition key; access patterns, skew, retention, and the measured deletion cost do.

## 2. A failing test

Before:

```text
I'm fixing the accumulator in ParcelBoard. The user wants this test green before the next release.
Earlier I renamed the helper, and now I'm checking the fixture setup. Here is the whole test file, including the unrelated label tests.
This runs on CPython 3.12.1, using pytest 8.2.2 on Linux 6.8 x86_64.
Calls without a bucket should each return a separate list; an explicitly supplied list must still be mutated in place.
The second default-bucket assertion fails with AssertionError: assert [1, 2] == [2].
What change makes the calls independent while preserving that explicit-bucket behavior?
```

```python
def label(item):
    return str(item)

def push(item, bucket=[]):
    bucket.append(item)
    return bucket

def test_integer_label():
    assert label(3) == "3"

def test_empty_label():
    assert label("") == ""

def test_explicit_bucket():
    bucket = [0]
    assert push(1, bucket) is bucket
    assert bucket == [0, 1]

def test_default_bucket():
    assert push(1) == [1]
    assert push(2) == [2]
```

After:

```text
Question: How should push give each default call a separate list while mutating an explicitly supplied list in place?
Stack: CPython 3.12.1; pytest 8.2.2; Linux 6.8 x86_64
Given: push(item, bucket=[]) appends item and returns bucket; the second default call returns [1, 2] instead of [2].
Constraints: Preserve in-place mutation and identity for an explicit list.
Ruled out: none
Evidence:
def push(item, bucket=[]):
    bucket.append(item)
    return bucket

def test_default_bucket():
    assert push(1) == [1]
    assert push(2) == [2]

AssertionError: assert [1, 2] == [2]
```

Cut release pressure, renaming history, and unrelated label tests because they cannot affect the assertion; retain the minimal failing function and test, and state the explicit-list contract in prose.

## 3. A performance question

Before:

```text
I am working on the endpoint in SignalDock, and the user requested a faster endpoint.
Earlier I added timings and then tried a larger pool. This is CPython 3.12.1 with asyncpg 0.29.0 and PostgreSQL 16.2 on Linux 6.8 x86_64.
At a fixed 1,200 requests/s for 10 minutes, eight workers with 10 pool connections each give p50 42 ms and p99 280 ms.
The per-request spans measure p99 pool wait at 210 ms and p99 SQL execution at 35 ms; database CPU averages 38%.
With 20 connections per worker at the same load, p99 is 310 ms and database CPU averages 91%.
No new dependencies are allowed, total connections must stay at or below 80, and p99 must fall below 200 ms.
Should I cap request concurrency before acquiring a database connection?
```

After:

```text
Question: Should request concurrency be capped before acquiring a database connection to reduce p99 latency?
Stack: CPython 3.12.1; asyncpg 0.29.0; PostgreSQL 16.2; Linux 6.8 x86_64
Given: 1,200 requests/s for 10 min; 8 workers, 10 connections each; p50 42 ms, p99 280 ms; per-request p99 pool wait 210 ms and SQL execution 35 ms; mean database CPU 38%.
Constraints: p99 below 200 ms; at most 80 total connections; no new dependencies.
Ruled out: 20 connections/worker at the same load → p99 310 ms, mean database CPU 91%.
Evidence: none
```

Cut project identity, requester, and instrumentation history because they do not locate the bottleneck; retain every measured number, its load conditions, and the larger-pool result without assuming percentiles add together.

## 4. A security / irreversibility question, written in Russian

До:

```text
Я делаю ротацию ключей в проекте «Архив-Север», пользователь требует закончить сегодня.
Сначала я перешифровал текущие данные ключом K2, потом проверил старую резервную копию.
Используются OpenSSL 3.2.1, AES-256-GCM и Linux 6.8 x86_64.
Резервным копиям с ключом K1 два дня, а хранить их нужно 30 дней с возможностью восстановления любой копии.
Старую копию не удалось расшифровать только ключом K2: «Ошибка проверки подлинности».
Удаление K1 необратимо. Мне нужно освободить хранилище ключей; можно ли удалить K1 сейчас?
```

После:

```text
Question: Можно ли удалить K1 сейчас без потери возможности восстановления?
Stack: OpenSSL 3.2.1; AES-256-GCM; Linux 6.8 x86_64
Given: Текущие данные перешифрованы K2; копиям с K1 2 дня, срок хранения 30 дней.
Constraints: Восстановление любой копии обязательно; удаление K1 необратимо.
Ruled out: Расшифровка старой копии только с K2 → ошибка проверки подлинности.
Evidence: Ошибка проверки подлинности
```

Убраны название проекта, требование пользователя, ход сессии и личная цель: они не меняют условия восстановления; сохранены возраст копий, срок хранения, ошибка и необратимость удаления.

## 5. Counter-example: what looks like narrative but is load-bearing

The exact library version, measured latency, and platform string below can look like incidental context. They define which implementation is under discussion, the available runtime environment, and whether the proposed change meets the limit.

After:

```text
Question: Should the event loop switch from asyncio to uvloop to meet the measured latency target on the deployment platform?
Stack: CPython 3.12.1; uvloop 0.19.0; deployment platform Linux 6.8 x86_64
Given: Standard asyncio has p99 14 ms at 5,000 messages/s over 10 min; uvloop 0.19.0 has p99 8 ms on the same deployment host with the same application, load, and duration.
Constraints: p99 at most 10 ms; keep CPython 3.12.1 and Linux 6.8 x86_64; only the event-loop implementation may change.
Ruled out: none
Evidence: none
```

The test is explicit: **a fact stays when a different value would produce a different answer.** Changing the platform to Windows Server 2022 would require a separate compatibility check and measurement; changing the baseline latency to 9 ms would remove the measured need for a replacement; changing the uvloop version would make the 8 ms result evidence about a different release, requiring a new measurement before deciding. Keep the version and platform attached to the numbers they qualify.

## 6. A sensitive finding, asked as a class

Before (fictional system and synthetic payload):

```text
I'm investigating a payment callback in NorthstarPay for customer Demo Shop.
The endpoint is https://callbacks.northstarpay.example/v1/payment-events. A forged paid status releases that customer's orders without payment.
This is CPython 3.12.1, standard-library hmac/hashlib with HMAC-SHA256, and JSON over HTTPS.
The sender signs only event_id; the receiver uses status from the same body to release orders.
In the local fixture, replacing {"event_id":"evt_demo_7","status":"pending"} with {"event_id":"evt_demo_7","status":"paid"} while retaining the original signature passes verification and releases the order.
The sender's signature format cannot change during the next 30 days, and unsigned status must never authorise a state change.
I need to patch this endpoint today; how should it handle callbacks until the sender changes?
```

After:

```text
Question: How should a receiver validate a callback when its HMAC covers only an identifier but unsigned fields drive state changes?
Stack: CPython 3.12.1; standard-library hmac/hashlib; HMAC-SHA256; JSON over HTTPS
Given: The sender authenticates an identifier only; the receiver trusts unsigned body fields across the external-to-internal trust boundary; altering them leaves verification successful.
Constraints: Sender signature format fixed for 30 days; unsigned fields must not authorise state changes.
Ruled out: HMAC verification alone → altered unsigned fields accepted.
Evidence: none
```

Removed the system, customer, hostname, endpoint, business impact, working payload, and task narration; signature coverage, the trust boundary, observed acceptance, and the fixed sender contract are enough to evaluate safe validation.

## 7. A planning packet

До:

```text
Я переношу очередь в проекте «Посылка-Юг», пользователь хочет закончить без потери заданий.
У нас CPython 3.12.1, psycopg 3.1.18 и PostgreSQL 16.2 на Linux 6.8 x86_64.
Проверка схемы показала: задания сейчас в таблице A, id — UUID PRIMARY KEY, payload — jsonb; таблица B с такой же схемой пуста.
По конфигурации один процесс пишет задания, четыре их обрабатывают. Повторная обработка уже безопасна: тесты повторов проходят.
Сначала я хотел переключить запись, но, наверное, надо перенести строки, потом как-то догнать изменения и переключить обработчики.
Остановить запись можно только на 30 секунд, потерять задания нельзя. Не знаю скорость копирования и как поймать записи в момент переключения.
Мне нужен порядок действий и способ откатиться, если всё сломается.
```

После:

```text
Objective: Перевести запись и обработку заданий с таблицы A на B без потерь.
Stack: CPython 3.12.1; psycopg 3.1.18; PostgreSQL 16.2; Linux 6.8 x86_64
Given: A: id UUID PRIMARY KEY, payload jsonb; B пуста, схема совпадает; 1 процесс записи, 4 обработчика; тесты повторной обработки проходят.
Constraints: Пауза записи до 30 с; потеря заданий недопустима.
Unknowns: Скорость копирования; способ учёта записей при переключении.
Done when: Запись и 4 обработчика используют B; сверка UUID не выявляет потерь; тесты повторов проходят; замер паузы до 30 с; откат проверен на копии.
```

Убраны рассказ от первого лица, название проекта, поручение и догадки о порядке; сохранены проверенная схема и конфигурация, ограничения и неизвестные, добавлены наблюдаемые критерии завершения.
