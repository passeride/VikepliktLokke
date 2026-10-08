# VikepliktLokke

Kartbasert proof of concept for å beregne hvor mange biler som må kjøre i en lukket sløyfe for at en ventende bil med vikeplikt **aldri** skal få en stor nok tidsluke – under en eksplisitt idealisert modell.

## Kom i gang

Krever Python 3.11+ og internettilgang for kartfliser / OpenStreetMap-data.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Åpne http://localhost:8000. Start med **Vis syntetisk demo**. For ekte OSM-veigeometri: klikk på et veikryss, velg hvilken vei trafikken kommer fra, og beregn. Reell veiforkjørsrett er ikke automatisk verifisert; brukeren må velge riktig vei selv.

Kjør tester: `pytest -q`.

## Hva POC-en gjør

- Kart (Leaflet / OSM), kryssvalg og retning via innkommende veisegment.
- OSMnx og NetworkX finner korteste lovlige *rettede* grafsløyfe som returnerer til den valgte retningen, uten umiddelbar U-sving. To alternativer: korteste distanse og korteste rundetid.
- Fartsgrenser leses fra OSM `maxspeed` der data finnes. Manglende / symbolske grenser bruker synlig, brukerdefinert fallback. Rundetiden summeres over alle veisegmenter.
- Beregning av **minimum antall biler som blokkerer alle tidsluker** under konstant rundetid og jevn fordeling, og maksimum antall gitt minimumsavstand.
- Animasjon av jevnt distribuerte biler langs rutens veigeometri. Syntetisk demonstrasjon krever ikke nedlasting av veinett.
- Sikkerhets- og metodebegrensninger tydelig merket i grensesnittet.

## Matematikk

La `T` være rundetid i sekunder, `v` være farten ved krysset i m/s, `l` gjennomsnittlig billengde i meter, og `g` nødvendig **fri tidsluke** fra bakenden av en bil til fronten av neste. Uniform bilstrøm med `N` biler gir:

```text
front-til-front-tid = T/N
fri tidsluke = T/N - l/v
N_min = floor(T / (g + l/v)) + 1
```

Streng ulikhet er brukt: Hvis luken er nøyaktig lik den kritiske luken, regnes den som akseptabel.

Maksimum biler baseres på at front-til-front-tid også må tilfredsstille minstekrav til fri følgeavstand og støtfangeravstand over hele ruten. Hvis `N_min > N_max`, er scenariet ikke realiserbart innen modellens parametere.

**Viktig:** Den matematiske modellen forutsetter identiske, perfekt jevnt fordelte biler og uforanderlig rundetid. Dette er ikke en prediksjon av faktisk menneskelig adferd.

## Vesentlige begrensninger (ikke kall dette en lovlig trafikkplan)

- OSMnx sin veggraf ivaretar kjøreretning, men her kontrolleres ikke alle svingeforbud, trafikkskilt, lysreguleringer, prioritet i kryss, vendeforbud eller rundkjøringsfelt.
- Det er ikke bekreftet at trafikken faktisk kan sirkulere uavbrutt, særlig om andre kryss, trafikklys og vikeplikter finnes på sløyfen.
- OSM-fartsgrensen kan mangle eller være feil. Fartsgrensen er heller ikke en realistisk simulert hastighetsprofil i kurver eller ved akselerasjon.
- Den korteste syklusen på OSM-grafen er ikke nødvendigvis den korteste **lovlig kjørbare** sløyfen.
- Ingen faktisk forkjørsrett eller valgt vikepliktig avkjøring valideres automatisk. Den valgte innkommende retningen representerer den trafikkstrømmen man vil undersøke.
- Fri tidsluke her er klaring fra bakende til front ved et punkt. Virkelig gap acceptance må tilpasses manøver, trafikkretning, sikkerhet og geometri.

## Foreslått neste iterasjon

1. Bruk NVDB for faktisk fartsgrense og vikeplikt/veireguleringer i Norge.
2. Modellér svingeregler i en edge-based graf og filtrer sløyfer som ikke er lovlige.
3. La brukeren velge vikepliktig manøver og konfliktstrømmer (svinge til høyre/venstre/krysse).
4. Bruk SUMO for køer, reaksjonstid, skiftende hastighet og stokastisk gap acceptance.
5. Test geografiske kartscenarioer med registrerte, kontrollerte OSM/ NVDB-data.

## Prosjektstruktur

```text
app/engine.py          Formler, rutesøk og demo
app/main.py            FastAPI og OSMnx
app/static/            Leaflet webgrensesnitt / animasjon
tests/test_engine.py   Modellerings- og rutealgoritmetester
.github/workflows/     Automatisk testkjøring
```
