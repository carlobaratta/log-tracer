# Log Tracer — Architettura

| | |
|---|---|
| Stato | Approvata per la v0.1.0 |
| Versione software di riferimento | 0.1.0 |
| Ultimo aggiornamento | 2026-10-23 |

Questo documento descrive **come** è costruito Log Tracer: moduli, dipendenze, modello dei dati, gestione degli errori, test e CI.
I requisiti a cui si fa riferimento (`ING-3`, `RUL-2`, …) sono definiti in [SPEC.md](SPEC.md).

---

## 1. Panoramica

Log Tracer è una pipeline lineare a stadi. Ogni stadio è una funzione pura, o quasi, che riceve dati immutabili e restituisce dati immutabili:

```
 percorsi ─▶ discovery ─▶ reader ─▶ parser (text | jsonl) ─▶ IngestionResult
                                                                  │
                                                       filtri (servizio, tempo)
                                                                  │
                                                                  ▼
                         correlation ─▶ statistics ─▶ rules ─▶ AnalysisResult
                                                                  │
                                                                  ▼
                                                    Report (metadati + risultati)
                                                     │            │           │
                                              terminal.py   json_report.py  state.py
                                               (Rich)        (stdout/file)  (.log-tracer/)
```

I tre principi strutturali sono questi:

1. **Il nucleo non conosce la CLI.** `ingestion` e `analysis` non importano né `typer` né `rich`, e si possono usare come libreria.
2. **I problemi di contenuto sono dati, i problemi d'ambiente sono eccezioni.** Una riga malformata produce un valore `UnparsedLine`, mentre un percorso inesistente solleva `InputError` (§6).
3. **Niente stato nascosto.** L'unico stato persistente è il report salvato da `analyze` (SPEC §8). L'orologio è iniettato e non letto dentro la pipeline.

---

## 2. Struttura del repository (obiettivo v0.1.0)

I moduli vengono introdotti progressivamente, solo quando servono. La colonna "Incremento" indica quando compare ciascun file (vedi §10).

```
log-tracer/
├── pyproject.toml              # metadati, dipendenze, configurazione di ruff, mypy, pytest e coverage
├── uv.lock                     # lockfile per CI riproducibile
├── README.md
├── SPEC.md
├── ARCHITECTURE.md
├── .github/workflows/ci.yml
├── src/log_tracer/
│   ├── __init__.py             # __version__
│   ├── __main__.py             # python -m log_tracer
│   ├── cli.py                  # Typer: argomenti → pipeline → rendering → codice di uscita
│   ├── errors.py               # gerarchia di eccezioni d'ambiente
│   ├── models.py               # dataclass ed enum puri, senza logica
│   ├── config.py               # AnalysisConfig, valori di default, caricamento e validazione TOML
│   ├── pipeline.py             # composizione ingestion → filtri → analysis → Report
│   ├── state.py                # salvataggio e lettura di last-report.json
│   ├── ingestion/
│   │   ├── __init__.py         # ingest(paths, input_format), ingest_files(files) -> IngestionResult
│   │   ├── discovery.py        # percorsi → sorgenti con formato + file saltati
│   │   ├── reader.py           # righe numerate, UTF-8/BOM/CRLF, sha256 incrementale
│   │   ├── fields.py           # normalizzazione condivisa: livelli, timestamp, ID, durate
│   │   ├── text_format.py      # parse_text_line
│   │   └── jsonl_format.py     # parse_jsonl_line
│   ├── analysis/
│   │   ├── __init__.py         # analyze(events, config) -> AnalysisResult
│   │   ├── fingerprint.py      # impronta dei messaggi
│   │   ├── correlation.py      # gruppi (union-find), ordine di timeline, precedes()
│   │   ├── statistics.py       # distribuzione dei livelli, gruppi di errori
│   │   └── rules/
│   │       ├── recurring_error.py
│   │       ├── high_latency.py
│   │       ├── warning_before_error.py
│   │       └── error_spike.py
│   └── reporting/
│       ├── json_report.py      # Report ⇄ dict (encode/decode), versione dello schema
│       └── terminal.py         # rendering Rich, con escape del contenuto dei log
├── tests/                      # vedi §8
├── examples/
│   ├── logs/                   # input della demo: analyze examples/logs/
│   │   ├── batch.log           # raffica di errori: error_spike e un finding high (D-23)
│   │   ├── sample.log
│   │   └── sample.jsonl
│   ├── log-tracer.toml         # soglie per servizio, usate nella demo
│   └── expected_report.json    # fuori da logs/, così non finisce tra i file saltati
├── .gitattributes              # examples/logs/** -text: stessi byte (e sha256) su ogni sistema
└── docs/demo.md                # percorso guidato; i comandi sono eseguiti da tests/e2e/test_demo.py
```

Rispetto alla struttura suggerita dalla specifica iniziale ci sono quattro differenze:

- `pipeline.py` evita che la CLI orchestri la logica e permette ai test d'integrazione di non passare dalla CLI.
- `errors.py` concentra in un solo punto le eccezioni che la CLI traduce in codici di uscita.
- `state.py` è la "persistenza" richiesta: è un modulo singolo, perché lo stato è un solo file.
- `analysis/rules/` contiene un file per regola, perché ogni regola ha comportamento, configurazione e test propri.

---

## 3. Responsabilità dei moduli

| Modulo | Responsabilità | Non deve |
|---|---|---|
| `models` | Definire i tipi del dominio (eventi, risultati, report) | Contenere logica di parsing, analisi o formattazione |
| `config` | Valori di default, caricamento TOML (`tomllib`), validazione → `ConfigError`. `parse_config()` valida dati già letti e si può testare senza file; `load_config()` aggiunge solo la lettura | Leggere opzioni della CLI o variabili d'ambiente |
| `errors` | `LogTracerError` → `InputError`, `ConfigError`, `StateError` | Conoscere i codici di uscita |
| `ingestion.discovery` | Espandere i percorsi, dedurre il formato, ordinare, deduplicare (ING-1…3) | Leggere il contenuto dei file |
| `ingestion.reader` | Produrre righe `(numero, testo)`, gestire la codifica (ING-4) e calcolare sha256 e dimensione in un'unica lettura | Interpretare le righe |
| `ingestion.fields` | Convertire valori grezzi in valori normalizzati e problemi di campo (§2.4 della SPEC) | Conoscere il formato di origine |
| `ingestion.*_format` | Trasformare **una** riga in `ParsedEvent` oppure `UnparsedLine`, come funzione totale | Sollevare eccezioni per contenuti non validi |
| `analysis.fingerprint` | Calcolare l'impronta del messaggio (§2.5 della SPEC) | — |
| `analysis.correlation` | Costruire i gruppi, ordinare la timeline, valutare la relazione `precedes` (COR-1…7) | Dedurre correlazioni dal tempo o dal testo |
| `analysis.statistics` | Calcolare conteggi per livello, intervallo temporale e gruppi di errori | Decidere la severità |
| `analysis.rules.*` | Implementare una regola: `AnalysisContext` → `RuleOutcome` | Dipendere da altre regole |
| `pipeline` | Comporre gli stadi, applicare i filtri, assemblare il `Report` | Stampare output o scrivere file |
| `reporting.json_report` | Serializzare e deserializzare il `Report` in modo canonico e deterministico | Decidere cosa contiene il report |
| `reporting.terminal` | Costruire oggetti Rich (tabelle, elenchi, timeline) a partire dal `Report`, con escape del contenuto dei log (CLI-4) | Stampare: decide la CLI dove scrivere; calcolare statistiche |
| `state` | Scrittura atomica e lettura di `last-report.json`, creazione di `.gitignore` | Rianalizzare i log |
| `cli` | Gestire argomenti e opzioni, chiamare pipeline e state, scegliere il rendering, creare le console Rich, impostare stdout e stderr in UTF-8, tradurre le eccezioni in codici di uscita | Contenere logica di dominio |

### 3.1 Interfaccia delle regole

Ogni regola è una funzione con firma esplicita:

```python
def run(ctx: AnalysisContext, config: RecurringErrorConfig) -> RuleOutcome: ...
```

`analysis/__init__.py` chiama le quattro regole in un ordine fisso. Non c'è registro dinamico né sistema di plugin: aggiungere una regola significa aggiungere un modulo e una riga. `AnalysisContext` contiene i dati calcolati una sola volta e condivisi da tutte le regole: eventi in ordine di timeline, gruppi di errori, sequenze correlate ed etichetta di sequenza di ogni evento. Così nessuna regola li ricalcola. Le quattro regole girano nell'ordine RUL-1…4 e non dipendono l'una dall'altra.

---

## 4. Direzione delle dipendenze

```
                      cli
        ┌──────────┬───┴──────┬──────────┐
        ▼          ▼          ▼          ▼
    pipeline   reporting    state      config
     │    │        │          │
     ▼    ▼        │          └──▶ reporting.json_report
ingestion analysis │
     │    │        │
     ▼    ▼        ▼
  ┌──────────────────────────┐
  │  models · config · errors│   nucleo: solo libreria standard
  └──────────────────────────┘
```

Regole:

1. Il nucleo (`models`, `config`, `errors`) importa solo la libreria standard e i propri moduli.
2. `ingestion` e `analysis` importano solo il nucleo e sono indipendenti tra loro.
3. `reporting` importa solo il nucleo. `rich` è ammesso solo in `reporting.terminal` e in `cli`.
4. `typer` è ammesso solo in `cli`.
5. Nessun modulo importa `cli`.

Queste regole sono verificate da `tests/unit/test_architecture.py`, che analizza gli `import` con `ast` (circa 40 righe e nessuna dipendenza aggiuntiva). Una violazione fa fallire la CI, così l'architettura descritta qui non può divergere silenziosamente dal codice.

---

## 5. Modello dei dati

Tutti i modelli sono `@dataclass(frozen=True, slots=True)` o enum della libreria standard:

- `frozen=True` impedisce la riassegnazione dei campi;
- le collezioni sono `tuple` oppure `Mapping`, un tipo che non espone metodi di modifica, quindi `mypy --strict` rifiuta ogni tentativo di mutazione;
- `slots=True` riduce la memoria per evento.

I modelli non contengono logica: niente parsing, niente regole, niente serializzazione.

### 5.1 Ingestion

```python
class Level(IntEnum):  # rango: confrontabile con >=, UNKNOWN sotto a tutto
    UNKNOWN = 0
    TRACE = 5
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class InputFormat(StrEnum):
    TEXT = "text"
    JSONL = "jsonl"


@dataclass(frozen=True, slots=True, order=True)
class SourceRef:
    path: str  # POSIX, come fornito dall'utente
    line: int  # numerazione da 1


@dataclass(frozen=True, slots=True)
class Event:
    ref: SourceRef
    timestamp: datetime | None  # se presente: sempre timezone-aware, UTC
    level: Level
    raw_level: str | None  # valore originale, utile per UNKNOWN
    message: str
    service: str | None
    request_id: str | None
    trace_id: str | None
    duration_ms: float | None
    attributes: Mapping[str, str]


class IssueCode(StrEnum):  # problemi di campo: l'evento resta valido
    TIMESTAMP_MISSING = "timestamp.missing"
    TIMESTAMP_INVALID = "timestamp.invalid"
    TIMESTAMP_ASSUMED_UTC = "timestamp.assumed_utc"
    LEVEL_MISSING = "level.missing"
    LEVEL_UNKNOWN = "level.unknown"
    MESSAGE_MISSING = "message.missing"
    MESSAGE_INVALID = "message.invalid"
    SERVICE_INVALID = "service.invalid"
    REQUEST_ID_INVALID = "request_id.invalid"
    TRACE_ID_INVALID = "trace_id.invalid"
    DURATION_INVALID = "duration_ms.invalid"
    ENCODING_INVALID = "encoding.invalid"


class UnparsedReason(StrEnum):
    NO_MATCH = "no_match"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_JSON = "invalid_json"
    NOT_AN_OBJECT = "not_an_object"
    NO_KNOWN_FIELDS = "no_known_fields"
    INVALID_JSON = "invalid_json"
    NOT_AN_OBJECT = "not_an_object"
    NO_KNOWN_FIELDS = "no_known_fields"


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event: Event
    issues: tuple[IssueCode, ...]


@dataclass(frozen=True, slots=True)
class UnparsedLine:
    ref: SourceRef
    reason: UnparsedReason
    excerpt: str  # al massimo 200 caratteri


LineOutcome = ParsedEvent | UnparsedLine  # risultato dei parser: totale, nessuna eccezione


@dataclass(frozen=True, slots=True)
class SourceSummary:
    path: str
    format: InputFormat
    size_bytes: int
    sha256: str
    lines: int
    blank_lines: int
    events: int
    unparsed: int
    error: str | None  # valorizzato se il file non è leggibile (ING-7)


@dataclass(frozen=True, slots=True)
class SkippedFile:  # file trovato in una directory e non analizzato (ING-1)
    path: str
    reason: SkipReason  # unsupported_extension, not_a_regular_file, ...


@dataclass(frozen=True, slots=True)
class DataWarning:  # stampato su stderr dalla CLI (ING-7, ING-9, COR-6)
    code: WarningCode  # unreadable_file, mostly_unparsed, large_correlation_group
    path: str | None  # None per i problemi trovati dall'analisi
    message: str


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    sources: tuple[SourceSummary, ...]
    skipped: tuple[SkippedFile, ...]
    unparsed_counts: Mapping[UnparsedReason, int]
    unparsed_samples: tuple[UnparsedLine, ...]  # solo i primi N
    issue_counts: Mapping[IssueCode, int]
    warnings: tuple[DataWarning, ...]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    events: tuple[Event, ...]
    summary: IngestionSummary
```

`IngestionResult` conserva i **conteggi** e solo un **campione** delle righe non interpretabili. In questo modo un file con un milione di righe malformate non occupa memoria proporzionale.

### 5.2 Analisi e report

```python
class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class RuleName(StrEnum):
    RECURRING_ERROR = "recurring_error"
    HIGH_LATENCY = "high_latency"
    WARNING_BEFORE_ERROR = "warning_before_error"
    ERROR_SPIKE = "error_spike"


@dataclass(frozen=True, slots=True)
class CorrelationGroup:
    label: str  # COR-5
    request_ids: tuple[str, ...]  # ordinati
    trace_ids: tuple[str, ...]  # ordinati
    events: tuple[Event, ...]  # in ordine di timeline (COR-3)


@dataclass(frozen=True, slots=True)
class ErrorGroup:  # alimenta top_errors e RUL-1
    service: str | None
    fingerprint: str
    count: int
    has_critical: bool
    first_seen: datetime | None
    last_seen: datetime | None
    events: tuple[SourceRef, ...]  # campione
    correlation_groups: int  # sequenze che contengono almeno un suo evento
    correlation_ids: tuple[str, ...]  # le loro etichette, per prima occorrenza (campione)


EvidenceValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class Finding:
    id: str  # f"{rule}:{sha256(chiave canonica)[:12]}"
    rule: RuleName
    severity: Severity
    title: str
    description: str
    evidence: Mapping[str, EvidenceValue]
    event_count: int
    events: tuple[SourceRef, ...]  # al massimo max_examples
    correlation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    rule: RuleName
    status: Literal["ok", "skipped"]
    reason: str | None
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    level_counts: Mapping[Level, int]
    time_range: tuple[datetime, datetime] | None
    correlation_groups: int
    uncorrelated_events: int
    top_errors: tuple[ErrorGroup, ...]
    rules: tuple[RuleOutcome, ...]
    findings: tuple[Finding, ...]  # ordinati (RUL-0.4)
    warnings: tuple[DataWarning, ...]  # ad esempio COR-6


@dataclass(frozen=True, slots=True)
class Report:
    tool_version: str
    generated_at: datetime
    config: AnalysisConfig
    filters: FilterSummary  # criteri (Filters), eventi esclusi, avvisi
    ingestion: IngestionSummary  # riepiloghi e campioni, senza eventi
    analysis: AnalysisResult
```

Il `Report` non contiene gli eventi (REP-2): `IngestionResult` è composto da `events` e da un `IngestionSummary` (sorgenti, conteggi, campioni), e nel report finisce solo quest'ultimo. `schema_version` non è un campo del modello: appartiene alla codifica JSON e la scrive `reporting.json_report`.

**Correlazione (`analysis.correlation`).** `correlate()` costruisce un *union-find* sui nodi `("request", id)` e `("trace", id)`: due nodi sono uniti quando compaiono nello stesso evento, e ogni componente connessa è una sequenza (COR-1). Lo spazio di nomi nel nodo tiene separati un request_id e un trace_id con lo stesso valore. Il costo è quasi lineare grazie all'unione per dimensione e alla compressione dei cammini. La partizione non dipende dall'ordine degli eventi: un test di proprietà la confronta con una visita in ampiezza scritta in modo indipendente nel test. `inspect` usa lo stesso risultato tramite `pipeline.run_inspect()`, che produce un `Inspection` (identificativo cercato, sequenze trovate, riepilogo degli input) senza eseguire le regole.

**Stato (`state`).** `save()` scrive il report con `json_report.dumps` in un file temporaneo della stessa directory, poi lo rinomina su `last-report.json`: chi legge vede il report precedente o quello nuovo, mai uno scritto a metà (STA-1). `load()` legge il file e lo decodifica con `json_report.loads`, che ricostruisce il `Report` e solleva `ReportDecodeError` con il percorso del valore errato (ad esempio `inputs[0].sha256`); `state` la traduce in `StateError`. Senza PATH, `inspect` passa per `pipeline.run_inspect_saved()`, che rilegge le sorgenti salvate con il loro formato tramite `ingestion.ingest_files()` (D-16), e `pipeline.changed_sources()` confronta gli sha256 per l'avviso di STA-3.

**Filtri (`pipeline.filter_events`).** Tra ingestion e analysis, `filter_events()` tiene gli eventi del perimetro (D-9) e restituisce un `FilterSummary` con i criteri normalizzati (servizi ordinati e senza duplicati, per NFR-2), il numero di eventi esclusi e gli avvisi `unmatched_service_filter`. Le regole ricevono solo gli eventi filtrati e non sanno che esistono filtri. Nel report gli avvisi compaiono nell'ordine degli stadi: ingestion, filtri, analisi. La CLI converte e valida le opzioni con `ingestion.fields.parse_time_bound()` prima di leggere i log.

**Identificativo di un finding.** È l'hash SHA-256 di una chiave canonica, specifica di ogni regola (ad esempio `servizio + "\x1f" + impronta` per RUL-1). È stabile tra esecuzioni e permette di confrontare due report. Non si usa `hash()`, che in Python cambia da un'esecuzione all'altra.

---

## 6. Gestione degli errori

| Categoria | Meccanismo | Dove viene gestita |
|---|---|---|
| Contenuto di una riga (malformata, campo non valido) | Valore di ritorno: `UnparsedLine` / `IssueCode` | Conteggiata nell'`IngestionResult` e mai sollevata |
| I/O su **un** file tra tanti | `OSError` intercettato **solo** attorno alla lettura di quel file | `SourceSummary.error`; l'analisi prosegue (ING-7) |
| Ambiente: percorsi, nessuna sorgente leggibile | `InputError` | `cli` → codice 3 |
| File di `--output` non scrivibile | `OutputError` | `cli` → codice 3, prima di stampare su stdout |
| Configurazione | `ConfigError`, con il nome della chiave | `cli` → codice 2 |
| Stato salvato mancante o incompatibile | `StateError` | `cli` → codice 3 |
| Identificativo non presente nei log (`inspect`) | `NotFoundError` | `cli` → codice 4 |
| Bug | Qualunque altra eccezione | Unica guardia in `cli.main()`: messaggio e traceback su stderr, codice 70 |

Il codice non contiene mai `except Exception` fuori dalla guardia finale della CLI. La traduzione da eccezione a codice di uscita avviene in un solo punto (`cli.py`), quindi la tabella della SPEC §5.4 corrisponde a un unico `match`.

Due casi limite della libreria standard sono stati verificati su Python 3.12 e 3.14 e vanno gestiti esplicitamente:

- `json.loads` su JSON troppo annidato solleva `RecursionError`, non `ValueError`; il parser intercetta entrambi.
- `json.loads` accetta `NaN` e `Infinity`, e `bool` è una sottoclasse di `int`; la normalizzazione di `duration_ms` e degli ID li esclude esplicitamente.

---

## 7. Determinismo

| Fonte di non determinismo | Contromisura |
|---|---|
| Ordine degli argomenti o del filesystem | Sorgenti deduplicate e ordinate per percorso (ING-3) |
| Ordinamento con valori uguali | Chiavi di ordinamento totali: `(timestamp, path, line)`, `(-count, service, fingerprint)`, `(-severity, rule, id)` |
| Iterazione su `set` e `dict` | Le uscite sono costruite da sequenze ordinate e mai iterando un `set` |
| `hash()` randomizzato | Gli ID dei finding usano `hashlib.sha256` |
| Orologio | `now` è un parametro di `pipeline.run_analysis`; la CLI passa `datetime.now(UTC)`, i test un valore fisso |
| Serializzazione | `json.dumps(..., ensure_ascii=False, allow_nan=False, indent=2)` con ordine delle chiavi fisso; `allow_nan=False` fa emergere eventuali NaN come bug |
| Percorsi Windows e POSIX | I percorsi sono esposti con `PurePath.as_posix()` |

---

## 8. Strategia di test

### 8.1 Livelli

| Livello | Cosa verifica | Strumenti | Velocità |
|---|---|---|---|
| Unitari | Parser, normalizzazione, impronta, correlazione, singole regole, configurazione, codec JSON, escape del terminale | pytest con parametrize; factory `make_event()` | ms |
| Basati su proprietà | I parser non sollevano mai eccezioni e producono solo testo UTF-8 valido (NFR-7); l'impronta è idempotente; un evento valido scritto in formato testo o JSONL e riletto dà gli stessi campi; la discovery non dipende dall'ordine degli argomenti | Hypothesis | < 5 s |
| Architettura | Le regole di §4 sugli import | `ast` | ms |
| Integrazione | `pipeline.run_analysis` su file reali in `tmp_path`: più file, directory, deduplica, file vuoto, file illeggibile, determinismo | pytest | < 1 s ciascuno |
| End-to-end | Comandi CLI: codici di uscita (SPEC §9), JSON valido su stdout, messaggi su stderr, stato in `tmp_path`, test golden su `examples/` | `typer.testing.CliRunner` | < 1 s ciascuno |
| Smoke del pacchetto | La wheel si installa e l'entry point funziona | job CI `package` | — |

### 8.2 Organizzazione

```
tests/
├── conftest.py          # profili di Hypothesis, blocco della rete (NFR-1)
├── factories.py         # make_event(), make_report(), T0: costruttori dei modelli
├── large_logs.py        # generatore deterministico di log grandi (D-27)
├── unit/
├── integration/
└── e2e/                 # conftest.py: stato salvato fuori dal repository
```

I casi limite non hanno file di fixture: ogni test scrive i propri log in `tmp_path`, così il contenuto è visibile accanto alle asserzioni.

- **Tracciabilità.** Ogni test cita il requisito che verifica, ad esempio `test_text_line_with_invalid_date_is_unparsed  # ING-6, §2.2`. Un requisito senza test è un requisito non verificato.
- **Rete.** Una fixture `autouse` sostituisce `socket.socket.connect` con una funzione che fallisce, così l'offline (NFR-1) è verificato a ogni esecuzione.
- **Test golden.** `examples/expected_report.json` viene confrontato con l'output di `analyze examples/logs/ --format json` dopo aver normalizzato `generated_at` (`tests/e2e/test_golden.py`). Il confronto è sui dati JSON e non sui byte, quindi non dipende dai fine riga del file golden; i log di esempio invece sono protetti da `.gitattributes`, perché le loro dimensioni e i loro sha256 compaiono nel report. Rigenerarlo è un'azione esplicita documentata in `docs/demo.md`, mai automatica.
- **Demo eseguibile.** `tests/e2e/test_demo.py` esegue in ordine ogni comando dei blocchi `bash` di `docs/demo.md`, da una copia di `examples/`, e verifica il codice di uscita indicato accanto (D-24).
- **Hypothesis.** In locale il profilo `dev` genera esempi casuali, così ogni esecuzione può trovare casi nuovi. In CI il profilo `ci` (`HYPOTHESIS_PROFILE=ci`) usa `derandomize=True`: gli esempi dipendono solo dal test, e un fallimento si riproduce rieseguendo lo stesso commit. In entrambi `deadline=None` evita fallimenti dovuti alla lentezza dei runner.
- **Grandi input.** `tests/integration/test_large_input.py`, marcato `@pytest.mark.slow`, genera 100 000 righe con `tests/large_logs.py` e verifica conteggi e risultati, **non i tempi**: le soglie di tempo in CI producono test instabili. I valori attesi vengono dalla contabilità del generatore, cioè da ciò che ha scritto, e mai dal codice sotto test (D-27). I test `slow` girano di default, anche in CI; `-m "not slow"` li esclude (D-28). I tempi si misurano a mano con lo stesso generatore e sono documentati in `docs/demo.md` (NFR-3).
- **Tabella degli errori.** `tests/e2e/test_error_table.py` ha un caso per ogni riga di SPEC §9, con comportamento, codice di uscita e dove l'esito è visibile, e verifica che i casi corrispondano alle righe della tabella nella SPEC (A6, D-29).

### 8.3 Casi limite richiesti dalla specifica iniziale

| Caso | Livello | Esito atteso |
|---|---|---|
| File vuoto | integrazione ed e2e | 0 eventi, report valido, codice 0 |
| File inesistente | e2e | stderr chiaro, codice 3 |
| JSON malformato | unitario ed e2e | `invalid_json` conteggiato; le altre righe sono analizzate |
| JSON troppo annidato | unitario | `invalid_json`, nessuna `RecursionError` propagata |
| Timestamp assenti | unitario e integrazione | Evento senza timestamp, in fondo alla timeline, escluso da RUL-3 e RUL-4 e conteggiato |
| Livelli sconosciuti | unitario | `UNKNOWN`, valore originale conservato |
| Identificativi mancanti | unitario e integrazione | Evento non correlato, presente nelle statistiche |
| Timestamp uguali | unitario | Ordine `(path, line)`; nessuna precedenza tra sorgenti diverse (COR-4) |
| Grandi quantità di righe | integrazione (slow) | Conteggi corretti, nessun errore |

### 8.4 Copertura

`pytest-cov` misura la copertura con i rami (`branch = true`) e una soglia minima `fail_under = 85`. La soglia serve a intercettare regressioni grossolane e non è un obiettivo: un test che esegue codice senza verificarne il comportamento non è accettato.

---

## 9. Tooling e CI

### 9.1 Scelte

| ID | Scelta | Motivazione | Alternativa |
|---|---|---|---|
| T-1 | Dataclass della libreria standard, senza Pydantic | Nessuna dipendenza; la validazione avviene nei parser, dove si conosce il contesto dell'errore | Pydantic: schema automatico, ma dipendenza pesante e validazione lontana dal parser |
| T-2 | `mypy --strict` | Puro Python, standard de facto, non richiede Node.js | Pyright: più veloce, ma richiede Node.js |
| T-3 | `uv` per lo sviluppo (`uv sync`, `uv run`), build con `hatchling` | Ambienti riproducibili con `uv.lock`; l'installazione resta standard (`pip install .`) | Solo pip e venv: nessun lockfile |
| T-4 | Hypothesis come dipendenza di sviluppo | Verifica in modo convincente la robustezza dei parser (NFR-7) | Solo casi scritti a mano |
| T-5 | Nessun registro di plugin per le regole | Quattro regole note non giustificano un meccanismo dinamico | Registro con decoratori |
| T-6 | Test di architettura con `ast` | Circa 40 righe, nessuna dipendenza | `import-linter` |
| T-7 | Python 3.12+ come requisito minimo e codice scritto per 3.12 | Imposto dalla specifica; la CI verifica 3.12, 3.13 e 3.14 | — |
| T-8 | Console Rich create a ogni stampa, con `markup`, `emoji` e `highlight` disattivati; `soft_wrap` su stderr | Rich interpreta `[bold]` e `:warning:` nelle stringhe, colora i numeri sui terminali e manda a capo a 80 colonne su stderr: l'output dipenderebbe dal contenuto dei log e dal terminale. Creandole al momento della stampa si leggono `NO_COLOR`, `COLUMNS` e le redirezioni correnti. | Una console globale configurata all'avvio |
| T-10 | La configurazione è scritta nel report da un codificatore esplicito, non da `dataclasses.asdict` | `asdict` fallisce sulle mappe in sola lettura (`MappingProxyType`, verificato) usate per le soglie per servizio; il codificatore esplicito fissa anche l'ordine alfabetico dei servizi | `asdict` con mappe modificabili, rinunciando all'immutabilità |
| T-9 | `main()` imposta stdout e stderr in UTF-8 con `reconfigure` | Su Windows l'output rediretto usa cp1252: un carattere come `→` faceva fallire la stampa con `UnicodeEncodeError` (verificato con `PYTHONIOENCODING=cp1252`). | Sostituire i caratteri non rappresentabili, che altererebbe il contenuto dei log |

Dipendenze:

- runtime: `typer`, `rich`;
- sviluppo: `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`.

### 9.2 Workflow `.github/workflows/ci.yml`

| Job | Ambiente | Passi |
|---|---|---|
| `quality` | ubuntu, Python 3.12 | `uv sync --locked` → `ruff check` → `ruff format --check` → `mypy` |
| `test` | ubuntu × {3.12, 3.13, 3.14}, più windows e macos con 3.14 | `uv run pytest --cov` (inclusi i test `slow`); report di copertura come artefatto |
| `package` | ubuntu, dopo `test` | `uv build` → installazione della wheel in un venv pulito → `log-tracer --version` → `log-tracer analyze examples/logs/ --format json` validato con `python -m json.tool`, poi `analyze` in testo, `top-errors`, `inspect` con e senza percorsi, `report` e un'analisi filtrata |

Il workflow si avvia su `push` verso `main` e su `pull_request`, con `permissions: contents: read`, `concurrency` per annullare le esecuzioni superate e un `timeout-minutes` su ogni job. Le action sono fissate alla versione maggiore.

---

## 10. Piano incrementale

| # | Incremento | Moduli introdotti |
|---|---|---|
| 1 | **Slice verticale:** file `.log` → eventi → `recurring_error` → report JSON su stdout; scheletro del progetto e CI | `models`, `errors`, `ingestion` (testo), `analysis.fingerprint`, `analysis.statistics`, `rules.recurring_error`, `reporting.json_report` (solo encode), `pipeline`, `cli` (solo `analyze`) |
| 2 | JSONL, directory, più file, codifiche, test basati su proprietà | `ingestion.jsonl_format`, `discovery` completo |
| 3 | Output nel terminale con Rich, `--format`, `top-errors`, `--output`, `--fail-on` (codici di uscita completi, tranne il 4) | `reporting.terminal` |
| 4 | Correlazione e `inspect ID PATH...` | `analysis.correlation` |
| 5 | Configurazione TOML e `high_latency` | `config` (caricamento), `rules.high_latency` |
| 6 | `warning_before_error` ed `error_spike` | due regole |
| 7 | Stato persistente, `report`, `inspect` senza percorsi, filtri | `state`, decode del report |
| 8 | Esempi, test golden, `docs/demo.md`, README | — |
| 9 | Consolidamento: grandi input, revisione dei casi limite, revisione della copertura | — |
| 10 | Margine, rifinitura, rilascio `v0.1.0` | — |

Tutti gli incrementi sono completati. Il rilascio `v0.1.0` è il tag annotato del commit su cui il workflow di CI è verde (D-32).

---

## 11. Compromessi noti ed evoluzioni possibili

| Compromesso | Conseguenza | Evoluzione |
|---|---|---|
| Tutti gli eventi in memoria | Implementazione semplice e ordinamento globale facile; il limite pratico è di qualche milione di righe | Ingestion in streaming per le statistiche e indice per gruppo su disco |
| Correlazione transitiva (union-find) | Un ID riutilizzato fonde sequenze non correlate; mitigato da COR-6 e dagli ID sentinella | Limite configurabile sulla dimensione dei gruppi |
| Finestre fisse per i picchi | Un picco a cavallo di due finestre può risultare diviso | Finestre scorrevoli con fusione |
| Report salvato e non ricalcolato | `report` riflette l'analisi passata anche se i file sono cambiati | Confronto tra due report salvati (`diff`) |
| `top-errors` esegue l'intera pipeline | Lavoro superfluo, trascurabile alle dimensioni della v0.1 | Pipeline selettiva per regole |
| Formato testuale unico | Altri formati richiedono una conversione preliminare | Un modulo `*_format.py` per ogni nuovo formato, senza modificare il resto |
| Nessun evento multi-riga nel testo (ex Q-2) | Ogni riga di uno stack trace dopo la prima è conteggiata come non interpretabile | Nella v0.2, considerare continuazione le righe che iniziano con spazio o tabulazione |
| Solo testo e JSON come output (ex Q-3) | Nessun report da condividere con chi non usa il terminale | Un report HTML generato dal JSON, senza cambiare l'analisi |
