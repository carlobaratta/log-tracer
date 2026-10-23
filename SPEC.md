# Log Tracer — Specifica funzionale

| | |
|---|---|
| Stato | Bozza in revisione |
| Versione software di riferimento | 0.1.0 |
| Ultimo aggiornamento | 2026-10-23 |

Questo documento descrive **cosa** fa Log Tracer v0.1.0 e come si comporta nei casi limite.
Il **come** (moduli, dipendenze, modello dei dati, test, CI) è descritto in [ARCHITECTURE.md](ARCHITECTURE.md).

Convenzioni:

- Ogni requisito ha un identificativo stabile (`ING-3`, `RUL-2`, …). I test che lo verificano lo citano nel nome o nella docstring.
- **Deve** indica un requisito obbligatorio per la v0.1.0; **dovrebbe** indica un requisito desiderabile ma non bloccante.
- Identificatori nel codice, chiavi JSON, opzioni e messaggi della CLI sono in inglese. La documentazione è in italiano (vedi Q-1).

---

## 1. Obiettivo e perimetro

Log Tracer è uno strumento a riga di comando, locale e offline, che:

1. legge file di log testuali e JSON Lines;
2. normalizza ogni riga in un evento con un modello comune;
3. correla gli eventi tramite `request_id` e `trace_id`;
4. ricostruisce le timeline delle richieste;
5. applica quattro regole diagnostiche deterministiche;
6. presenta un riepilogo nel terminale ed esporta un report JSON.

**Principio guida.** I risultati sono *indizi diagnostici*, non prove. Una correlazione temporale non dimostra una relazione causale. I testi prodotti dal programma usano formulazioni come "precede" o "coincide con" e non usano mai "causa".

### 1.1 Fuori perimetro per la v0.1.0

| Escluso | Motivo / conseguenza |
|---|---|
| Eventi multi-riga nel formato testuale (stack trace) | Ogni riga di continuazione è conteggiata come non interpretabile. Nel formato JSONL lo stack trace sta in un campo e non pone problemi. |
| File compressi, lettura da stdin, monitoraggio in tempo reale | Possibili estensioni future. |
| Timestamp numerici (epoch) e formati diversi da ISO 8601 | Un timestamp numerico è trattato come non valido. |
| Campi annidati nei JSON (`{"service": {"name": …}}`) | L'oggetto annidato finisce negli attributi, ma non viene mappato sui campi del modello. |
| Rilevamento del formato dal contenuto | Il formato si deduce dall'estensione oppure si forza con `--input-format`. |
| SQLite, dashboard web, report HTML, machine learning e AI generativa | Fuori dai requisiti della prima versione. Il report HTML è opzionale e successivo. |

### 1.2 Glossario

| Termine | Significato |
|---|---|
| Sorgente | Un file di input. |
| Evento | Una riga interpretata e normalizzata nel modello `Event`. |
| Riga non interpretabile | Una riga che non può diventare un evento. È sempre conteggiata insieme al motivo. |
| Problema di campo | Un evento valido con un campo mancante, non valido o completato da un'assunzione (ad esempio un timestamp assente). |
| Livello | La gravità dichiarata dalla riga di log (`TRACE` … `CRITICAL`, oppure `UNKNOWN`). |
| Severità | La gravità di un finding (`low`, `medium`, `high`). È un concetto distinto dal livello. |
| Impronta (*fingerprint*) | Il messaggio normalizzato usato per raggruppare errori simili (§2.5). |
| Gruppo di correlazione | Un insieme di eventi collegati da `request_id` e/o `trace_id` (§3). |
| Timeline | Gli eventi di un gruppo ordinati nel tempo. |
| Finding | Il risultato di una regola: tipo, severità, descrizione, eventi coinvolti ed evidenze. |
| Evidenza | Un insieme di valori misurati (conteggi, soglie, durate) che giustifica un finding. |

---

## 2. Ingestion e normalizzazione

### 2.1 Requisiti generali

- **ING-1** L'input è costituito da uno o più percorsi, file o directory. Le directory sono visitate ricorsivamente. File e directory nascosti (con nome che inizia per `.`) sono ignorati senza segnalazione. Ogni altro file trovato e non analizzato è elencato in `skipped_files`, con uno di questi motivi:
  - `unsupported_extension`: estensione non riconosciuta;
  - `not_a_regular_file`: link rotto, FIFO o dispositivo, che non vengono mai letti perché potrebbero bloccare la lettura;
  - `symlinked_directory`: link simbolico a una directory, non seguito perché potrebbe uscire dall'albero o formare un ciclo;
  - `unreadable_directory`: directory di cui non si può leggere il contenuto.

  Un percorso esplicito che non è un file regolare produce un errore di input.
- **ING-2** Il formato si deduce dall'estensione:
  - `.log` e `.txt` corrispondono al formato `text`;
  - `.jsonl` e `.ndjson` corrispondono al formato `jsonl`.

  L'opzione `--input-format text|jsonl` sceglie il parser al posto dell'estensione. Il valore di default `auto` usa l'estensione. L'opzione cambia **come** si leggono i file, non **quali** file si leggono:
  - dentro una directory i file sono sempre selezionati per estensione, e quelli con altre estensioni sono saltati (`skipped_files`), così un `README.md` non viene mai letto come log;
  - un file indicato esplicitamente con un'estensione non riconosciuta è accettato solo con `--input-format`; senza, produce un errore di input.
- **ING-3** Se lo stesso file è raggiunto più volte, dopo la risoluzione del percorso, viene analizzato una sola volta. Le sorgenti sono elaborate in ordine lessicografico di percorso, indipendentemente dall'ordine degli argomenti.
- **ING-4** I file sono letti come UTF-8 e un eventuale BOM iniziale è ignorato. I byte non validi sono sostituiti con U+FFFD e l'evento riceve il problema `encoding.invalid`. Sono supportati i terminatori di riga LF e CRLF.
- **ING-5** Le righe vuote o composte solo da spazi non sono né eventi né righe non interpretabili. Sono conteggiate come `blank_lines`.
- **ING-6** Le righe non interpretabili non sono mai scartate in silenzio. Il report contiene:
  - il totale;
  - la ripartizione per motivo;
  - i primi N esempi (percorso, riga, motivo ed estratto troncato a 200 caratteri), con N = `report.max_examples` (default 10).
- **ING-7** Un file illeggibile (permessi, rimozione durante l'esecuzione, errore di I/O) è registrato nel report con il relativo errore e l'analisi prosegue sugli altri file. Se nessuna sorgente è leggibile, si verifica un errore di input.
- **ING-8** Ogni evento conserva il percorso della sorgente e il numero di riga, che parte da 1.
- **ING-9** Se in un file almeno il 50% delle righe non vuote è non interpretabile, viene prodotto un avviso `mostly_unparsed`, che indica la percentuale (arrotondata per difetto) e il formato usato. Un file illeggibile (ING-7) produce un avviso `unreadable_file`. Gli avvisi finiscono in `data_quality.warnings` e la CLI li stampa su stderr; per `mostly_unparsed` aggiunge il suggerimento di usare `--input-format`.

### 2.2 Formato testuale v1 (`text`)

Ogni riga ha questa struttura:

```
<timestamp> <LEVEL> [<service>] <message> [<key>=<value> ...]
```

| Elemento | Regola |
|---|---|
| Separatori | Uno o più spazi o tabulazioni. |
| `timestamp` | Obbligatorio. Ha la forma `YYYY-MM-DD`, seguita da `T` o da uno spazio, seguita da `HH:MM:SS`. Può avere una frazione di secondo (`.` o `,`) e un fuso (`Z`, `±HH:MM` o `±HHMM`). Le ore vanno da 00 a 23, come in RFC 3339: `24:00:00` non è accettato (Python 3.14 lo accetterebbe, 3.12 e 3.13 no). |
| `LEVEL` | Obbligatorio. È una parola tutta maiuscola (`[A-Z]+`), mappata secondo §2.4. Un valore non riconosciuto diventa `UNKNOWN` e il valore originale viene conservato. |
| `[service]` | Opzionale. È il testo tra parentesi quadre (senza `]`), privo degli spazi esterni. `[]` equivale a nessun servizio. |
| `message` | È tutto ciò che precede i campi finali e può essere vuoto (problema `message.missing`). |
| Campi finali | Sono token `key=value` posti **in fondo** alla riga. La chiave rispetta `[a-z_][a-z0-9_.]*` e il valore è non vuoto e senza spazi. Un `key=value` seguito da altre parole resta nel messaggio. |
| Chiavi riconosciute | `request_id`, `trace_id` e `duration_ms`. Le altre chiavi finiscono negli attributi. Se una chiave è duplicata, prevale l'ultima. |

Una riga è non interpretabile per uno di questi motivi:

- `no_match`: non rispetta la struttura;
- `invalid_timestamp`: la forma è corretta ma la data non esiste, ad esempio `2026-02-30`.

Esempi:

| Riga | Esito |
|---|---|
| `2026-10-16T14:03:22.120Z INFO [api-gateway] POST /checkout request_id=req-184 trace_id=4bf92f35` | Evento `INFO`, servizio `api-gateway`, messaggio `POST /checkout`, con request_id e trace_id |
| `2026-10-16 14:03:24,902 ERROR [payments] Gateway timeout duration_ms=2500` | Evento `ERROR` senza ID; il timestamp senza fuso è assunto UTC |
| `2026-10-16T14:03:25Z WARN retry attempt=2 scheduled` | Evento `WARNING` senza servizio; `attempt=2` resta nel messaggio |
| `2026-10-16T14:03:25Z NOTICE [db] vacuum done` | Evento `UNKNOWN` (valore originale `NOTICE`) |
| `14:03:25 ERROR something` | Non interpretabile: `no_match` |
| `2026-02-30T10:00:00Z ERROR [x] y` | Non interpretabile: `invalid_timestamp` |
| `    at com.example.Foo.bar(Foo.java:42)` | Non interpretabile: `no_match` (continuazione multi-riga, §1.1) |

### 2.3 Formato JSON Lines (`jsonl`)

- **JL-1** Ogni riga non vuota deve contenere un oggetto JSON. Una riga è non interpretabile per uno di questi motivi:
  - `invalid_json`: la sintassi non è valida, inclusa una nidificazione eccessiva;
  - `not_an_object`: il JSON è valido ma non è un oggetto;
  - `no_known_fields`: l'oggetto non contiene nessuno tra timestamp, livello e messaggio.
- **JL-2** I campi si riconoscono tramite alias. Se ne sono presenti più di uno, vale il primo nell'ordine della tabella e gli altri restano negli attributi. Un valore `null` equivale a un campo assente: si passa all'alias successivo, e un alias `null` non viene conservato negli attributi. Se una chiave è duplicata, vale l'ultima occorrenza.

  | Campo | Alias, in ordine di priorità |
  |---|---|
  | timestamp | `timestamp`, `@timestamp`, `ts`, `time` |
  | livello | `level`, `severity`, `levelname`, `lvl` |
  | messaggio | `message`, `msg` |
  | servizio | `service`, `service_name`, `app` |
  | request_id | `request_id`, `requestId`, `req_id` |
  | trace_id | `trace_id`, `traceId` |
  | duration_ms | `duration_ms`, `durationMs` |

- **JL-3** I tipi sono convertiti secondo questa tabella. Un valore non accettato rende assente il campo e registra il problema `<campo>.invalid`.

  | Campo | Valori accettati |
  |---|---|
  | timestamp | Stringa ISO 8601 con la stessa forma di §2.2. La parte oraria è obbligatoria: `2026-10-16` da solo non è valido. |
  | livello | Stringa, senza distinzione tra maiuscole e minuscole. Qualunque altro valore (ad esempio il numero `30`) diventa `UNKNOWN`, conserva il valore originale in JSON compatto e registra `level.unknown`. Un livello assente registra `level.missing`. |
  | messaggio | Stringa. Un valore non stringa è convertito in JSON compatto e registra `message.invalid`. |
  | servizio | Stringa non vuota. |
  | request_id, trace_id | Stringa, oppure intero convertito in stringa decimale. I booleani non sono accettati. |
  | duration_ms | Numero o stringa numerica, finito e ≥ 0. I booleani, `NaN` e `Infinity` non sono accettati. |

- **JL-4** I campi non riconosciuti finiscono negli attributi. I valori che non sono stringhe sono serializzati in JSON compatto con chiavi ordinate.
- **JL-6** Le sequenze di escape JSON come `"\ud800"` producono surrogati isolati, che non sono caratteri Unicode validi e impedirebbero di scrivere il report in UTF-8. Vengono sostituiti con U+FFFD e l'evento registra `encoding.invalid`, una sola volta anche se il problema compare in più campi.
- **JL-5** Se un timestamp manca o non è valido, l'evento viene comunque creato senza timestamp e con il problema `timestamp.missing` o `timestamp.invalid`.

### 2.4 Normalizzazione dei campi

**Livelli** (il formato testuale richiede il maiuscolo; il formato JSONL non distingue maiuscole e minuscole):

| Canonico | Valori accettati | Rango |
|---|---|---|
| `TRACE` | trace | 5 |
| `DEBUG` | debug | 10 |
| `INFO` | info | 20 |
| `WARNING` | warning, warn | 30 |
| `ERROR` | error, err | 40 |
| `CRITICAL` | critical, crit, fatal | 50 |
| `UNKNOWN` | qualsiasi altro valore, oppure valore assente | 0 |

Un evento è un *errore* se il suo livello è `ERROR` o `CRITICAL`. `UNKNOWN` non soddisfa mai una soglia di livello.

**Timestamp.** Un timestamp con fuso è convertito in UTC. Un timestamp senza fuso è **assunto UTC** e registra il problema `timestamp.assumed_utc`. Internamente tutti i timestamp sono UTC. In output usano il formato RFC 3339 con `Z` e microsecondi.

**Identificativi.** Gli spazi esterni sono rimossi. I seguenti valori equivalgono a un identificativo assente:

- la stringa vuota;
- `-`, `null`, `none` e `n/a`, senza distinzione tra maiuscole e minuscole;
- le stringhe composte solo da zeri, come il trace id W3C non valido.

`request_id: "abc"` e `trace_id: "abc"` sono identificativi distinti.

**Servizio.** Gli spazi esterni sono rimossi. Una stringa vuota equivale a un servizio assente.

### 2.5 Impronta dei messaggi

Gli errori simili si raggruppano tramite un'impronta del messaggio. Le sostituzioni sono applicate in quest'ordine:

1. UUID (`8-4-4-4-12` cifre esadecimali) → `<uuid>`
2. IPv4, con porta opzionale → `<ip>`
3. Token esadecimali di almeno 8 caratteri che contengono sia cifre sia lettere `a-f`, con prefisso `0x` opzionale → `<hex>`
4. Ogni sequenza di cifre, con eventuale parte decimale → `<num>`
5. Gli spazi consecutivi sono compressi e quelli esterni rimossi.

Esempio: `Payment 7f3c9a12 failed after 2500 ms (attempt 3)` diventa `Payment <hex> failed after <num> ms (attempt <num>)`.

L'impronta è idempotente: applicarla due volte dà lo stesso risultato. Le stringhe tra virgolette non sono normalizzate, perché gli apostrofi rendono la regola ambigua.

---

## 3. Correlazione e timeline

- **COR-1** Due eventi appartengono allo stesso gruppo se condividono un `request_id` o un `trace_id`, direttamente o in modo transitivo: un gruppo è una componente connessa del grafo eventi–identificativi. Esempio: A (`req=1`, `trace=T`), B (`req=1`) e C (`trace=T`) formano un unico gruppo.
- **COR-2** Gli eventi senza `request_id` e senza `trace_id` sono *non correlati*. Contribuiscono a statistiche, errori frequenti, latenze e picchi. Sono esclusi dalle regole di sequenza (RUL-3) e da `inspect`.
- **COR-3** La timeline ordina gli eventi per timestamp. A parità di timestamp ordina per percorso della sorgente e poi per numero di riga. Gli eventi senza timestamp vanno in fondo e sono marcati.
- **COR-4** A *precede* B se entrambi hanno un timestamp e vale una di queste condizioni:
  - `A.ts < B.ts`;
  - `A.ts == B.ts`, A e B provengono dalla stessa sorgente e la riga di A viene prima.

  Se due sorgenti diverse riportano lo stesso timestamp, l'ordine è *indeterminato* e non conta come precedenza.
- **COR-5** L'etichetta di un gruppo è il `trace_id` lessicograficamente minore. Se il gruppo non ha trace_id, è il `request_id` minore. Il report elenca comunque tutti gli identificativi del gruppo. I gruppi sono ordinati per etichetta. Se due gruppi hanno la stessa etichetta (un valore usato come trace_id in uno e come request_id nell'altro), viene prima quello con trace_id.
- **COR-6** Un gruppo con più di 10 000 eventi genera un avviso di qualità dei dati `large_correlation_group` (con `path` nullo), perché potrebbe indicare un identificativo riutilizzato. Come gli altri avvisi, finisce in `data_quality.warnings` e la CLI lo stampa su stderr (CLI-2).
- **COR-7** Non esiste alcuna correlazione per vicinanza temporale o per contenuto del messaggio.

L'ordine tra eventi di servizi diversi riflette i timestamp riportati, che possono essere affetti da sfasamento degli orologi. La CLI lo segnala nell'intestazione di `inspect`.

- **COR-8** `inspect ID` cerca l'identificativo sia tra i request_id sia tra i trace_id. Il confronto è esatto e distingue maiuscole e minuscole; gli spazi esterni vengono rimossi come nei log. Se il valore compare come request_id in un gruppo e come trace_id in un altro, vengono mostrati entrambi i gruppi. Un ID che nei log significa "assente" (`-`, `null`, `0`…, §2.4) è un errore d'uso (codice 2); un ID mai visto termina con il codice 4.
- **COR-9** La timeline testuale mostra per ogni evento:
  - l'ora UTC, solo l'ora del giorno se la sequenza cade in un unico giorno;
  - lo scarto dal primo evento con timestamp, in millisecondi sotto il secondo e in secondi con tre decimali sopra;
  - livello, servizio e messaggio;
  - su una seconda riga, sorgente e riga, durata e attributi.

  Gli eventi senza timestamp sono in fondo e marcati `no time`. Un evento con lo stesso timestamp del precedente ma proveniente da un altro file è marcato `~`, e una legenda spiega che il loro ordine è indeterminato (COR-4).

---

## 4. Regole diagnostiche

Requisiti comuni a tutte le regole:

- **RUL-0.1** Ogni regola è deterministica e documentata in questa sezione.
- **RUL-0.2** Ogni finding contiene:
  - `id`: stabile tra esecuzioni identiche;
  - `rule`;
  - `severity`;
  - `title`;
  - `description`;
  - `evidence`: valori leggibili da un programma;
  - `event_count`;
  - `events`: al massimo `max_examples` riferimenti, per default 10, in ordine di timeline;
  - `correlation_ids`: opzionale.
- **RUL-0.3** Il report indica lo stato di ogni regola: `ok` oppure `skipped`, con un motivo (ad esempio `no_duration_data`).
- **RUL-0.4** I finding sono ordinati per severità decrescente, poi per regola e poi per `id`.

### RUL-1 `recurring_error` — errori ricorrenti

| Aspetto | Comportamento |
|---|---|
| Input | Eventi con livello `ERROR` o `CRITICAL`. |
| Raggruppamento | Per (servizio, impronta). Il servizio assente forma un gruppo a sé. |
| Condizione | Il gruppo conta almeno `min_occurrences` eventi (default 3). |
| Severità | `high` se il gruppo conta almeno `high_count` eventi (default 20) o contiene un `CRITICAL`; altrimenti `medium`. |
| Evidenze | `service`, `fingerprint`, `count`, `min_occurrences`, `high_count`, `has_critical`, `first_seen`, `last_seen`, `correlation_groups` |

Gli stessi gruppi alimentano anche la sezione `top_errors` del report (i primi `report.top_errors` gruppi) e il comando `top-errors` (i primi `--limit`). Entrambi mostrano i gruppi anche sotto soglia, ordinati per conteggio decrescente, poi per servizio e poi per impronta.

### RUL-2 `high_latency` — latenze elevate

| Aspetto | Comportamento |
|---|---|
| Input | Eventi con `duration_ms`. Le durate **non** sono mai ricavate dalla differenza tra timestamp, per via dello sfasamento degli orologi. |
| Soglie | `warn_ms` (default 1000) e `critical_ms` (default 5000), sovrascrivibili per servizio (§7). Gli eventi senza servizio usano le soglie globali e formano un gruppo a sé. |
| Condizione | Si produce un finding per ogni servizio con almeno un evento la cui durata è **strettamente maggiore** di `warn_ms`: una durata uguale alla soglia non è lenta. |
| Severità | `high` se almeno un evento ha durata strettamente maggiore di `critical_ms`; altrimenti `medium`. |
| Evidenze | `service`, `warn_ms`, `critical_ms` (le soglie effettive del servizio), `slow_events`, `measured_events`, `max_ms`, `p95_ms`. Il p95 è calcolato con il metodo *nearest-rank* su tutti gli eventi misurati del servizio: è il valore in posizione ⌈0,95·n⌉ fra le durate ordinate, quindi sempre un valore osservato. |
| Eventi | Solo gli eventi lenti, dal più lento al più veloce, a parità di durata in ordine di timeline. `correlation_ids` contiene le sequenze di questi eventi, nello stesso ordine. |
| Skip | `no_duration_data` se nessun evento ha `duration_ms`. |

### RUL-3 `warning_before_error` — warning che precedono errori

| Aspetto | Comportamento |
|---|---|
| Input | Gruppi di correlazione che contengono almeno un errore con timestamp. |
| Algoritmo | Sia E il primo errore (`ERROR` o `CRITICAL`) con timestamp del gruppo, secondo COR-3; gli errori successivi non contano. I precursori sono gli eventi di livello esattamente `WARNING` che precedono E secondo COR-4. Ogni coppia (servizio + impronta del warning, servizio + impronta dell'errore) è aggregata su tutti i gruppi, e ogni gruppo conta una sola volta per coppia. Se in un gruppo lo stesso warning compare più volte, vale il più vicino a E. |
| Condizione | Il warning precede con certezza E in almeno un gruppo. |
| Severità | `low`; `medium` se la coppia ricorre in almeno `medium_min_groups` gruppi (default 3). |
| Descrizione | Ad esempio: "In 3 correlated sequences the warning "X" (payments) precedes the error "Y" (payments). This is a temporal correlation, not proven causation." |
| Evidenze | `warning_service`, `warning_fingerprint`, `error_service`, `error_fingerprint`, `groups`; `median_gap_ms`, cioè la mediana, sui gruppi, dell'intervallo tra il warning più vicino ed E (calcolata in microsecondi interi); `undetermined_groups`, cioè i gruppi in cui il warning compare ma il suo ordine rispetto a E non si può stabilire (senza timestamp, oppure stesso timestamp di E ma in un altro file). Una coppia osservata solo in gruppi indeterminati non produce un finding. |
| Eventi | Per ogni gruppo, in ordine di E: il warning più vicino ed E, alternati. `event_count` vale 2 × `groups`; `correlation_ids` sono le etichette dei gruppi nello stesso ordine. |
| Skip | `no_correlated_errors` se non esistono gruppi con errori. |

### RUL-4 `error_spike` — picchi di errori

| Aspetto | Comportamento |
|---|---|
| Finestre | Finestre fisse (*tumbling*) di `window_s` secondi (default 60), allineate all'epoch Unix in UTC. |
| Intervallo | Va dalla finestra del primo evento con timestamp a quella dell'ultimo, considerando tutti i livelli. |
| Riferimento | `baseline` è la mediana dei conteggi di errori su **tutte** le finestre dell'intervallo, incluse quelle a zero. Viene calcolata dalle sole finestre con errori (le altre valgono zero e stanno in testa all'ordinamento), così un timestamp anomalo lontano anni non crea milioni di finestre in memoria. |
| Condizione | Una finestra è un picco se `errors ≥ max(min_errors, factor × baseline)`. I default sono `min_errors` = 5 e `factor` = 3.0. Le finestre di picco consecutive formano un unico finding. |
| Severità | `high` se la finestra peggiore ha almeno il doppio della soglia di errori (`peak ≥ 2 × threshold`); altrimenti `medium`. |
| Evidenze | `window_s`, `start`, `end`, `windows` (finestre unite), `errors`, `peak_window_errors`, `baseline`, `threshold`, `excluded_without_timestamp` |
| Eventi | Gli errori delle finestre del picco, in ordine di timeline; `correlation_ids` sono le loro sequenze. L'id del finding dipende dall'inizio del picco. |
| Skip | `insufficient_data` se l'intervallo copre meno di 3 finestre. |
| Limiti noti | Una finestra fissa può spezzare un picco a cavallo di due finestre. Gli errori senza timestamp sono esclusi e conteggiati nelle evidenze. |

---

## 5. Interfaccia a riga di comando

### 5.1 Comandi

| Comando | Scopo | Scrive stato? |
|---|---|---|
| `log-tracer analyze PATH...` | Esegue l'analisi completa e stampa un riepilogo testuale o, con `--format json`, il report (§6). | Sì (§8) |
| `log-tracer top-errors PATH...` | Mostra solo gli errori più frequenti, come elenco o, con `--format json`, come documento JSON ridotto (REP-5). | No |
| `log-tracer inspect ID [PATH...]` | Mostra la timeline del gruppo che contiene l'ID, cercato sia come request_id sia come trace_id (COR-8, COR-9). Senza PATH usa gli input dell'ultima analisi salvata (STA-3). | No |
| `log-tracer report` | Mostra o esporta il report dell'ultima analisi salvata, senza rieseguirla. | No |
| `log-tracer --version` | Stampa la versione. | No |

### 5.2 Opzioni

| Opzione | Comandi | Significato |
|---|---|---|
| `--format text\|json` | tutti | Formato dello stdout. Il default è `text`. |
| `--output FILE` | `analyze`, `report` | Scrive **anche** il report JSON su FILE, qualunque sia il valore di `--format`. |
| `--input-format auto\|text\|jsonl` | `analyze`, `top-errors`, `inspect` | Forza il formato di input. Il default è `auto`, cioè per estensione. |
| `--config FILE` | `analyze`, `top-errors` | File di configurazione TOML (§7). |
| `--service NAME` | `analyze`, `top-errors` | Limita l'analisi a uno o più servizi. È ripetibile. Il nome è confrontato in modo esatto, maiuscole e minuscole comprese; un evento senza servizio è escluso. |
| `--since TS`, `--until TS` | `analyze`, `top-errors` | Limita l'analisi a un intervallo temporale: `--since` incluso, `--until` escluso (D-18). TS è un timestamp ISO 8601 (senza fuso è UTC) oppure una data sola, intesa come mezzanotte UTC (D-20). Quando sono attivi, gli eventi senza timestamp sono esclusi e conteggiati. |
| `--limit N` | `top-errors` | Numero di gruppi da mostrare, almeno 1. Il default è 10. Può superare `report.top_errors`. |
| `--fail-on low\|medium\|high` | `analyze` | Esce con codice 1 se esiste un finding di severità pari o superiore. L'output viene comunque stampato per intero e su stderr compare il motivo, ad esempio `Found 3 findings with severity medium or higher (--fail-on medium)`. |
| `--no-save` | `analyze` | Non salva lo stato. |
| `--state-dir DIR` | `analyze`, `inspect`, `report` | Directory dello stato. Il default è `.log-tracer`; si può impostare anche con la variabile `LOG_TRACER_STATE_DIR`. |

I filtri `--service`, `--since` e `--until` definiscono il **perimetro dell'analisi**: le regole vedono solo gli eventi filtrati. Il report registra i filtri attivi. I filtri sono controllati prima di leggere i log: un TS non valido, `--since` non precedente a `--until` o un nome di servizio vuoto terminano con codice 2. Un `--service` che non corrisponde a nessun evento letto produce l'avviso `unmatched_service_filter` (D-19).

### 5.3 Contratto di output

- **CLI-1** Lo stdout contiene solo l'output richiesto. Con `--format json` contiene esattamente un documento JSON valido, terminato da un newline. Stdout e stderr sono sempre in UTF-8, anche quando la localizzazione indica un'altra codifica: su Windows, ad esempio, l'output rediretto userebbe cp1252, che non può rappresentare molti caratteri presenti nei log.
- **CLI-2** Avvisi, errori e suggerimenti vanno su stderr.
- **CLI-3** I colori sono usati solo se l'output è un terminale. La variabile `NO_COLOR` è rispettata.
- **CLI-4** Il contenuto dei log mostrato nel terminale è sempre trattato come testo letterale: markup di Rich (`[bold]`) e codici emoji (`:warning:`) non vengono interpretati. Sono mostrati come escape visibili (ad esempio `\x1b`, `\n`, `\u202e`):
  - i caratteri di controllo (C0, DEL, C1), che potrebbero agire sul terminale;
  - i caratteri che cambiano la direzione del testo (U+061C, U+200E, U+200F, U+202A–U+202E, U+2066–U+2069), che potrebbero far apparire il testo in un ordine diverso da quello reale;
  - i separatori di riga e di paragrafo (U+2028, U+2029).
- **CLI-5** Nell'output testuale i timestamp sono mostrati in UTC.
- **CLI-6** Ogni messaggio d'errore indica cosa è successo e, quando possibile, come rimediare. Ad esempio: `No saved analysis in .log-tracer/. Run 'log-tracer analyze PATH' first.` I messaggi su stderr non vanno mai a capo, così si possono cercare e confrontare così come sono.
- **CLI-7** Il contenuto del riepilogo testuale non dipende dalla larghezza del terminale. Su un terminale stretto il testo va a capo ma non viene mai tagliato, con due sole eccezioni a lunghezza fissa: i titoli dei finding (70 caratteri) e i pattern degli errori (120 caratteri), accorciati con `…`. I gruppi di errori sono mostrati come elenco su due righe e non come tabella, perché una tabella con timestamp completi comprimerebbe il pattern fino a farlo sparire.

### 5.4 Codici di uscita

| Codice | Significato |
|---|---|
| 0 | Successo, anche quando ci sono finding o righe non interpretabili. |
| 1 | È stata raggiunta la soglia di `--fail-on`. |
| 2 | Errore d'uso o di configurazione: opzione non valida, file di configurazione non valido, ID di `inspect` che significa "assente". |
| 3 | Errore di input, output o stato: percorso inesistente, nessun file supportato, nessuna sorgente leggibile, file di `--output` non scrivibile, nessuna analisi salvata, report salvato incompatibile. |
| 4 | `inspect`: identificativo non trovato. |
| 70 | Errore interno, cioè un bug. Viene stampato un messaggio con il traceback su stderr. |

---

## 6. Report JSON

- **REP-1** Il report è un oggetto JSON con le chiavi seguenti, sempre presenti e in quest'ordine:

| Chiave | Contenuto |
|---|---|
| `schema_version` | Ad esempio `"1.0"`. Un consumatore deve verificare la versione maggiore. |
| `tool` | `{ "name": "log-tracer", "version": "0.1.0" }` |
| `generated_at` | Istante di generazione (RFC 3339, UTC). |
| `config` | La configurazione effettiva usata (§7), dopo l'unione di default e file; le soglie per servizio sono già unite a quelle globali ed elencate in ordine alfabetico. |
| `filters` | `services` (in ordine alfabetico, senza duplicati), `since` e `until` attivi, oppure `[]` e `null`; `excluded_events` (eventi letti ma esclusi dai filtri) ed `excluded_without_timestamp` (quelli esclusi perché senza timestamp mentre un limite temporale è attivo). |
| `inputs` | Per ogni sorgente: `path`, `format`, `size_bytes`, `sha256`, `lines`, `blank_lines`, `events`, `unparsed`, `error`. |
| `skipped_files` | Per ogni file saltato: `path` e `reason` (ING-1), in ordine di percorso. |
| `summary` | `events` (gli eventi nel perimetro, dopo i filtri; `inputs[].events` conta invece tutti gli eventi letti), `unparsed_lines`, `time_range` (`start` e `end`), `levels` (conteggio per ogni livello, inclusi quelli a zero), `correlation_groups`, `uncorrelated_events`, `findings_by_severity` |
| `data_quality` | `unparsed_by_reason`, `unparsed_samples`, `field_issues` (conteggio per problema, ad esempio `timestamp.assumed_utc`), `warnings` (per ogni avviso: `code`, `path`, `message`; ING-9) |
| `rules` | Per ogni regola: `rule`, `status` e `reason`. |
| `top_errors` | Per ogni gruppo: `service`, `fingerprint`, `count`, `first_seen`, `last_seen`, `events`, `correlation_groups` (numero di sequenze che contengono almeno un suo evento), `correlation_ids` (le loro etichette, in ordine di prima occorrenza, al massimo `max_examples`). |
| `findings` | Elenco dei finding (§4). |

- **REP-2** Gli eventi sono riferiti come `{"path": …, "line": …}`. Il report non contiene il contenuto integrale dei log, fatta eccezione per gli estratti delle righe non interpretabili.
- **REP-3** I percorsi sono riportati come forniti dall'utente, in notazione POSIX. Il report non aggiunge percorsi assoluti che potrebbero esporre informazioni locali.
- **REP-4** Per lo stesso input e la stessa configurazione, due report differiscono solo in `generated_at`.
- **REP-5** `top-errors --format json` produce un documento con le sole chiavi `schema_version`, `tool`, `generated_at`, `filters`, `inputs`, `skipped_files` e `top_errors` (D-21). Ognuna ha la stessa forma che ha nel report completo, così lo stesso codice può leggere entrambi i documenti.
- **REP-6** `inspect --format json` produce un documento con le chiavi `schema_version`, `tool`, `generated_at`, `query`, `inputs`, `skipped_files` e `sequences`. Ogni sequenza contiene `label`, `request_ids`, `trace_ids`, `services`, `event_count`, `time_range` ed `events`. A differenza del report, che cita gli eventi solo come riferimenti (REP-2), qui gli eventi sono completi, perché mostrarne il contenuto è lo scopo di `inspect`. Ogni evento contiene `path`, `line`, `timestamp`, `level`, `raw_level`, `service`, `message`, `request_id`, `trace_id`, `duration_ms` e `attributes`.

---

## 7. Configurazione

- **CFG-1** Le soglie si configurano con un file TOML passato con `--config`. Il programma non cerca file di configurazione in modo implicito.
- **CFG-2** Le chiavi sconosciute e i valori non validi producono un errore di configurazione (codice 2). Il messaggio indica file, chiave e valore, ad esempio `log-tracer.toml: high_latency.warn_ms must be a positive number, got -5`. Per una chiave sconosciuta viene suggerita quella più simile, se esiste. Le regole sono queste:
  - i conteggi sono interi positivi;
  - le soglie in millisecondi sono numeri positivi e finiti;
  - i booleani non valgono come numeri;
  - `critical_ms` deve essere maggiore di `warn_ms`, anche per ogni servizio dopo aver ereditato i valori globali;
  - `recurring_error.high_count` deve essere maggiore o uguale a `min_occurrences`.

  - `error_spike.window_s` è un intero di secondi, e `error_spike.factor` deve essere almeno 1: un fattore minore porterebbe la soglia sotto il livello normale.
- **CFG-3** La precedenza è: valori di default, poi file di configurazione, poi opzioni della CLI che corrispondono a una chiave (oggi solo `top-errors --limit`, che prevale su `report.top_errors`). Le soglie di un servizio ereditano da `high_latency` quelle che non specificano. I filtri CLI non sono configurazione e sono registrati a parte nel report.
- **CFG-4** Un file mancante o illeggibile, non UTF-8 o con sintassi TOML non valida (il messaggio riporta riga e colonna) è un errore di configurazione (codice 2). La configurazione viene controllata prima di leggere i log, così un errore non fa sprecare un'analisi lunga.

| Chiave | Default |
|---|---|
| `report.max_examples` | 10 |
| `report.top_errors` | 10 |
| `recurring_error.min_occurrences` | 3 |
| `recurring_error.high_count` | 20 |
| `high_latency.warn_ms` | 1000 |
| `high_latency.critical_ms` | 5000 |
| `high_latency.services.<nome>.warn_ms` / `.critical_ms` | — |
| `warning_before_error.medium_min_groups` | 3 |
| `error_spike.window_s` | 60 |
| `error_spike.factor` | 3.0 |
| `error_spike.min_errors` | 5 |

```toml
# examples/log-tracer.toml
[high_latency]
warn_ms = 1000
critical_ms = 5000

[high_latency.services.payments]   # critical_ms ereditato: 5000
warn_ms = 3000

[high_latency.services.api-gateway] # warn_ms ereditato: 1000
critical_ms = 2700
```

---

## 8. Stato persistente

- **STA-1** Al termine, `analyze` salva il report JSON in `<state-dir>/last-report.json` con una scrittura atomica (file temporaneo seguito da rename), a meno che sia indicato `--no-save`. Alla prima scrittura crea `<state-dir>/.gitignore` con contenuto `*`.
- **STA-2** `report` legge `last-report.json` e non rianalizza i log. Se il file manca, termina con codice 3 e un suggerimento. Se la versione maggiore dello schema è incompatibile, termina con codice 3.
- **STA-3** Senza PATH, `inspect` rilegge le sorgenti elencate in `inputs` del report salvato, risolvendole rispetto alla directory corrente. Se lo sha256 di una sorgente è cambiato, stampa un avviso su stderr. Se una sorgente non esiste più, termina con codice 3.
- **STA-4** Solo `analyze` scrive lo stato.

Il report salvato è lo stesso documento di `--format json`. `report` lo decodifica e lo ricodifica, quindi `report --format json` produce gli stessi byte che `analyze --format json` aveva stampato (D-14). Sono accettate tutte le versioni minori dello schema `1.x`; le chiavi sconosciute sono ignorate. Un report salvato danneggiato (JSON non valido, chiave mancante, valore di tipo sbagliato) è trattato come incompatibile: codice 3, con il percorso del valore errato e il suggerimento di rieseguire `analyze`.

---

## 9. Gestione degli errori

| Situazione | Comportamento | Dove è visibile | Codice |
|---|---|---|---|
| Percorso inesistente | Si ferma prima dell'analisi | stderr | 3 |
| Directory senza file supportati | Si ferma ed elenca i file saltati | stderr | 3 |
| File esplicito con estensione non riconosciuta | Si ferma e suggerisce `--input-format` | stderr | 3 |
| Un file illeggibile tra diversi file | Prosegue | stderr e `inputs[].error` | 0 |
| Nessuna sorgente leggibile | Si ferma | stderr | 3 |
| File vuoto | Zero eventi; il report è valido | report | 0 |
| Riga che non rispetta il formato | Riga non interpretabile `no_match` | `data_quality` | 0 |
| JSON malformato o troppo annidato | Riga non interpretabile `invalid_json` | `data_quality` | 0 |
| Timestamp assente o non valido (JSONL) | Evento senza timestamp | `data_quality.field_issues` | 0 |
| Timestamp senza fuso | Assunto UTC | `data_quality.field_issues` | 0 |
| Livello sconosciuto | `UNKNOWN`, con il valore originale conservato | `summary.levels` | 0 |
| Identificativi mancanti | Evento non correlato | `summary.uncorrelated_events` | 0 |
| `duration_ms` non valido | Campo assente | `data_quality.field_issues` | 0 |
| Opzione o configurazione non valida | Si ferma | stderr | 2 |
| Nessuna analisi salvata | Si ferma con un suggerimento | stderr | 3 |
| ID non trovato in `inspect` | Si ferma con un messaggio | stderr | 4 |
| ID di `inspect` che significa "assente" (`-`, `null`, `0`) | Si ferma con un messaggio d'uso | stderr | 2 |
| File di `--output` non scrivibile | Si ferma prima di stampare su stdout | stderr | 3 |
| Soglia di `--fail-on` raggiunta | Stampa l'output per intero, poi il motivo | stdout e stderr | 1 |
| Errore inatteso | Messaggio e traceback | stderr | 70 |

Gli errori di contenuto (righe e campi) non interrompono mai l'analisi. Gli errori d'ambiente (percorsi, permessi, configurazione) la interrompono solo quando non resta nulla di sensato da analizzare.

---

## 10. Requisiti non funzionali

- **NFR-1 Offline.** Nessun accesso alla rete. I test lo verificano bloccando i socket.
- **NFR-2 Determinismo.** A parità di input e configurazione, il risultato è identico tranne `generated_at`, indipendentemente dall'ordine degli argomenti e dall'ordine in cui il filesystem restituisce i file.
- **NFR-3 Prestazioni (indicative).** 100 000 righe (circa 15 MB) dovrebbero essere analizzate in meno di 10 secondi su un portatile recente, con memoria lineare nel numero di eventi. La misura è documentata in `docs/demo.md` e non è un vincolo della CI. Sulla macchina di riferimento 100 000 righe richiedono circa 1,2 s, e tempo e memoria crescono in modo lineare fino a 400 000 righe.
- **NFR-4 Portabilità.** Python 3.12, 3.13 e 3.14, su Linux, macOS e Windows.
- **NFR-5 Dipendenze.** A runtime le sole dipendenze sono `typer` e `rich`.
- **NFR-6 Qualità statica.** `ruff check` e `ruff format --check` non segnalano problemi; `mypy --strict` non segnala errori.
- **NFR-7 Robustezza.** Nessun contenuto di riga può causare un'eccezione non gestita. È verificato con test basati su proprietà.
- **NFR-8 Copertura.** La copertura dei rami è misurata, con una soglia minima dell'85% che serve contro le regressioni e non come obiettivo.

---

## 11. Criteri di accettazione v0.1.0

| # | Criterio | Verifica |
|---|---|---|
| A1 | Si installa seguendo il README | Job CI `package`: build della wheel, installazione in un ambiente pulito, `log-tracer --version` |
| A2 | Analizza `text` e `jsonl` | Test d'integrazione su `examples/` |
| A3 | Normalizza in modo coerente | Test unitari parametrizzati per ING, JL e §2.4 |
| A4 | Esegue le 4 regole documentate | Test unitari per RUL-1…4, inclusi i casi di skip |
| A5 | Produce un riepilogo testuale e un JSON valido | Test e2e: `json.loads(stdout)` e confronto con `examples/expected_report.json` |
| A6 | Gestisce gli input malformati come da §9 | Test e2e per ogni riga della tabella (`tests/e2e/test_error_table.py`) |
| A7 | La suite di test è verde | `pytest` in CI su tutta la matrice |
| A8 | Lint e tipi senza errori | `ruff check`, `ruff format --check`, `mypy --strict` in CI |
| A9 | CI funzionante | Workflow verde su GitHub |
| A10 | Demo riproducibile | `docs/demo.md` con comandi eseguibili su `examples/` |
| A11 | Nessuna dipendenza remota | NFR-1 e nessuna credenziale richiesta |

---

## 12. Decisioni e assunzioni

| ID | Decisione | Motivazione | Alternativa scartata |
|---|---|---|---|
| D-1 | Il formato testuale è definito da noi (§2.2) | La specifica richiede un formato esplicito; i campi `key=value` finali sono comuni e facili da analizzare senza ambiguità. | Supportare più formati diffusi (Apache, syslog): più lavoro, poco valore dimostrativo nella v0.1. |
| D-2 | Il formato si sceglie per estensione, con `--input-format` per forzarlo | È prevedibile e spiegabile. | Riconoscimento dal contenuto: euristico e con errori silenziosi. |
| D-3 | Un timestamp senza fuso è assunto UTC, con un problema di campo | Rende confrontabili tutti i timestamp; l'assunzione è tracciata. | Timestamp naive come "non confrontabili": complica ogni regola. |
| D-4 | Nel testo un timestamp non valido rende la riga non interpretabile; in JSONL l'evento resta, senza timestamp | Nel testo il timestamp è l'àncora che identifica il formato; in JSONL la struttura è data dal JSON. | Un'unica regola per entrambi i formati. |
| D-5 | La correlazione usa le componenti connesse su request_id e trace_id | Gestisce i log misti (il gateway registra solo request_id, i servizi entrambi). | Precedenza `trace_id` > `request_id`: spezza le sequenze miste. |
| D-6 | Le durate vengono solo da `duration_ms`, mai dai timestamp | Lo sfasamento degli orologi tra servizi rende inaffidabili le differenze. | Calcolare la durata di un gruppo come ultimo − primo evento. |
| D-7 | `analyze` salva il report; `report` lo rilegge; `inspect` rilegge le sorgenti | Il report è già l'artefatto richiesto e non serve un secondo formato di persistenza. Gli eventi completi non sono salvati. | (a) Comandi senza stato con PATH obbligatorio; (b) salvare solo l'elenco degli input e ricalcolare ogni volta. |
| D-8 | Le soglie si leggono solo da un file TOML esplicito | `tomllib` è nella libreria standard; nessuna configurazione implicita. | Ricerca automatica di `log-tracer.toml` o `pyproject.toml`. |
| D-9 | I filtri CLI restringono il perimetro dell'analisi | La semantica è unica e chiara; il report registra i filtri. | Filtri solo di visualizzazione, che confondono quando il JSON non coincide con l'output testuale. |
| D-10 | Nessun filtro `--level` in `analyze` | Escludere i WARNING annullerebbe RUL-3. | — |
| D-11 | Il formato predefinito di stdout è `text`; il JSON si ottiene con `--format json` o `--output` | Uno strumento da terminale si legge da una persona; il JSON resta completo e stabile per i programmi. | JSON come predefinito: illeggibile a colpo d'occhio. |
| D-12 | `--fail-on` e `--output` anticipati all'incremento 3 | Dipendono solo da componenti già presenti e completano i codici di uscita (tranne il 4, legato a `inspect`). | Rimandarli all'incremento 7. |
| D-13 | RUL-3 misura l'incertezza con `undetermined_groups` invece di `excluded_without_timestamp` | Conta l'incertezza proprio sulla coppia warning→errore del finding, inclusi i timestamp uguali in file diversi (COR-4). | Contare gli eventi senza timestamp, un dato poco legato al finding. |
| D-14 | `report --format json` ricodifica il report decodificato invece di copiare il file | L'output resta canonico ed è validato a ogni lettura; un test verifica che decode ed encode siano inversi. | Copiare i byte del file: un file danneggiato arriverebbe su stdout senza errori. |
| D-15 | `has_critical` di un gruppo di errori, assente dal report, si ricostruisce dal finding `recurring_error` dello stesso gruppo; senza finding vale falso | Serve solo a calcolare la severità di RUL-1, che è già nel finding; lo schema resta invariato. | Aggiungere `has_critical` a `top_errors`, cambiando lo schema. |
| D-16 | `inspect` senza PATH rilegge ogni sorgente con il formato salvato in `inputs[].format`; un `--input-format` esplicito lo sostituisce | Un'analisi fatta con `--input-format jsonl` su file `.log` resta coerente quando la si ispeziona. | Scegliere di nuovo il formato per estensione. |
| D-17 | `analyze` salva lo stato dopo `--output` e prima di stampare su stdout, anche quando poi scatta `--fail-on`; se il salvataggio fallisce esce con codice 3 senza stampare | Come per `--output`, nessun output parziale; `--fail-on` riguarda l'esito, non il salvataggio. | Salvare dopo la stampa: stdout completo ma codice 3, ambiguo per uno script. |
| D-18 | `--since` è incluso e `--until` è escluso | Intervalli semiaperti consecutivi (`--until X`, poi `--since X`) non si sovrappongono e non lasciano buchi. | Entrambi inclusi: un evento sul confine finirebbe in due analisi. |
| D-19 | `summary.events` conta gli eventi nel perimetro, `inputs[].events` quelli letti; un servizio di `--service` assente da tutti gli eventi letti produce un avviso | Il riepilogo descrive ciò che le regole hanno visto, gli input descrivono i file; un nome sbagliato non deve dare in silenzio un'analisi vuota. Un servizio presente ma fuori dall'intervallo temporale non è un errore e non genera avvisi. | Nessun avviso: un errore di battitura sembrerebbe un sistema senza problemi. |
| D-20 | `--since` e `--until` accettano i timestamp dei log e anche una data sola (mezzanotte UTC) | Lo stesso formato dei log evita sorprese; la data sola è il caso d'uso più comune. | Accettare espressioni relative (`1h`, `yesterday`): dipendono dall'orologio e rendono il report non riproducibile. |
| D-21 | Anche il documento di `top-errors` include `filters` | Senza, un elenco di errori filtrato sembrerebbe quello completo. | Lasciare REP-5 invariato. |
| D-22 | Lo schema resta `1.0` anche con la chiave `filters` | La v0.1.0 non è ancora stata rilasciata, quindi non esistono consumatori da proteggere; un report salvato prima dell'aggiunta viene rifiutato (`missing key 'filters'`) con il suggerimento di rieseguire `analyze`. | Passare a `1.1` e trattare `filters` come facoltativo. |
| D-23 | Gli esempi includono `batch.log`, una raffica di errori di un job notturno | La demo mostra tutte e quattro le regole e un finding `high` con la configurazione di default, senza cambiare gli altri due file, che restano la base dei test esistenti. | Modificare `sample.log`: cambierebbe la timeline di `req-184` e i test che la verificano. |
| D-24 | I comandi di `docs/demo.md` sono eseguiti da un test che ne controlla il codice di uscita; gli output nel documento sono estratti mantenuti a mano | La demo non può citare comandi che non funzionano; confrontare gli output legherebbe il test alla resa esatta di Rich. | Snapshot completi dell'output testuale. |
| D-25 | Il test golden riguarda solo il report JSON di `analyze examples/logs/` | È la fonte da cui derivano riepilogo, `top-errors` e `report`, che hanno già test mirati. | Golden anche per il testo e per i documenti di `top-errors` e `inspect`. |
| D-26 | `docs/demo.md` è in inglese, come il README | È la pagina pensata per chi valuta il progetto (Q-1); SPEC e ARCHITECTURE restano in italiano. | Demo in italiano. |
| D-27 | Il generatore di log grandi sta in `tests/large_logs.py`; i valori attesi del test `slow` vengono dalla sua contabilità | Il generatore non fa parte del prodotto; contare ciò che si scrive è indipendente dal codice sotto test. Lo stesso generatore serve alla misura di NFR-3. | Valori attesi calcolati eseguendo l'analisi una volta e salvandoli. |
| D-28 | I test `slow` girano di default, in locale e in CI; `-m "not slow"` li esclude | Durano pochi secondi, e un test che non gira di default viene dimenticato. | Escluderli di default con `addopts`. |
| D-29 | Le righe di §9 sono verificate da un unico test e2e parametrizzato, un caso per riga, anche quando sono coperte altrove | A6 diventa verificabile riga per riga; un test confronta i casi con la tabella, così aggiungere una riga senza test fa fallire la suite. | Lasciare la copertura sparsa nei test dei singoli comandi. |
| D-30 | Le righe scoperte si coprono con test, senza `pragma: no cover`; la soglia resta all'85% | La soglia protegge dalle regressioni (NFR-8); escludere codice dalla misura nasconderebbe proprio le parti difensive. | Escludere `__main__.py` e le guardie difensive dalla copertura. |

## 13. Questioni aperte

| ID | Domanda | Proposta |
|---|---|---|
| Q-1 | Lingua di README e documentazione: italiano o inglese? | Codice e CLI in inglese. Documentazione in italiano per ora; un README in inglese è consigliabile per il portfolio. |
| Q-2 | Supporto multi-riga nel formato testuale? | Escluso nella v0.1.0. Nella v0.2 si potrebbero considerare continuazione le righe che iniziano con spazio o tab. |
| Q-3 | Report HTML? | Solo dopo il completamento di tutti i criteri di §11. |
