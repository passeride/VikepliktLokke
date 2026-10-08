# VikepliktLokke

Velg et kryss eller en utkjøring på kartet, angi hvilken retning trafikken med forkjørsrett kommer fra, og finn **korteste bilveisløkke og minste bilantall som holder alle luker for små** i en idealisert modell. Bilene animeres langs veigeometrien. Du kan prøve én bil mindre og se en stor nok luke åpne seg.

## Start

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Åpne **http://localhost:8000**. På Linux/macOS kan du bruke `make run` i stedet. `make run HOST=0.0.0.0` gjør appen tilgjengelig på lokalnettet. Posisjonsknappen krever nettlesertillatelse og HTTPS eller localhost; stedssøk og koordinater fungerer uten posisjonstilgang.

Med Docker:

```bash
docker compose up --build -d
```

Compose publiserer på localhost:8000. Endre portbindingen hvis du vil eksponere appen på ditt lokalnett. Kjør **én serverprosess**: kartøkter og en avgrenset 10-minutters områdebuffer ligger i minnet. Etter omstart må punktet velges igjen.

## Bruk

1. Søk etter et sted, skriv `62.7379, 7.1608`, bruk posisjonsknappen, eller panorer og zoom i kartet.
2. Velg **veikryss** eller **utkjøring langs veien**, og klikk nær punktet. Kryssvalg finner en faktisk gren i veinettet innen 150 m. Utkjøringsvalg projiserer til bilveien innen 100 m og deler veisegmentet i begge kjøreretninger. Selve utkjøringen trenger ikke finnes i OSM.
3. Velg eventuelt veien der den vikepliktige bilen står. Velg innkommende prioritert trafikk fra nummererte veier på kartet eller nedtrekkslisten. Du bekrefter selv forkjørsretten.
4. Appen beregner både **korteste distanse** og **korteste rundetid**. Bytt mellom dem. Den raskeste løkken gir færrest biler for den samme innkommende retningen, men er ikke nødvendigvis den korteste.
5. Resultatet viser lengde, rundetid, minste bilantall, fri tidsluke, fri avstand ved punktet og om avstandskravene tillater bilantallet. Endre fart ved manglende data, billengde eller ønsket luke for å beregne på nytt.
6. Endre **Prøv bilantall**, for eksempel minimum minus én. Punktet blir grønt når modellen gir nok gjenværende fri tid til å kjøre ut. Pause eller juster animasjonstempoet.
7. Under **Rute, sammenligning og datagrunnlag** finnes veisegmenter, sammenligning, GeoJSON-eksport og en delbar lenke til punkt og parametere.

Den stiplede rammen viser søkeområdet. Dersom ingen løkke finnes, prøv en annen retning eller et større område (opptil 5 km). Resultatet er **korteste løkke i det nedlastede og filtrerte veinettet**; større områder kan finne andre løkker. Et område rundt et nærliggende tidligere klikk kan gjenbrukes; den viste rammen er alltid den faktiske avgrensningen.

**Syntetisk demo** fungerer uten OSM-oppslag. Leaflet leveres lokalt; kartfliser, stedssøk og reelle veier krever nett. Nettfeil vises i grensesnittet, og et mislykket nytt rutesøk tømmer det gamle resultatet. Overpass prøver to tjenester; `OVERPASS_URL` kan settes til en annen kompatibel tjeneste. Stedssøk sendes bare når du trykker Søk, er mellomlagret og begrenset til maksimalt én forespørsel per sekund.

## Rutesøk og OSM-data

Ett Overpass-oppslag henter veier, noder og svingeregler fra samme svar. NetworkX lagrer en **rettet multigraf med opprinnelige OSM-noder**. Vi beholder parallelle veier, énveisretninger, implisitt énveisretning for rundkjøringer og motorveier, samt numeriske retningsbestemte fartsgrenser. Fartsgrenser i mph konverteres til km/t. Manglende, symbolske eller betingede fartsgrenser bruker den synlige brukerdefinerte farten.

Dijkstra søker over **innkommende veisegmenter som tilstander**. Dermed kan den kontrollere svingen mellom hvert par av segmenter, også svingen mellom siste og første runde. Umiddelbar reversering på samme OSM-vei er utelatt overalt. Node-baserte `no_*` og `only_*`-svingeregler for motorbil kontrolleres. Private veier, destinasjonstilgang, parkeringsganger, private/mindre serviceutkjøringer og betinget adgang er utelatt. Mer spesifikk biladgang overstyrer generelle adgangstags.

Via-way, betingede og andre ikke-støttede svingeregler behandles **konservativt ved å utelate fra-veiene**. Antallet utelatte regler vises. Dette kan utelate en mulig rute og betyr at resultatet ikke nødvendigvis er kortest blant alle lovlige ruter. Kontroller i kartgrunnlaget er ikke det samme som en bekreftet lovlig eller uavbrutt kjørbar løkke.

## Beregning

La `T` være rundetid, `v` fart ved konfliktpunktet (m/s), `l` billengde, og `g` nødvendig **fri tidsluke fra bakenden av én bil til fronten av neste**. Med `N` jevnt tidsforskjøvede biler:

```text
front-til-front-tid h = T / N
fri tidsluke = h - l / v
N_min = floor(T / (g + l / v)) + 1
fri bilavstand ved punktet = v * h - l
```

En luke lik `g` regnes som akseptabel. Derfor må blokkerende luker være **strengt mindre**, og én bil mindre gir minst den nødvendige luken. Rundetiden summeres fra lengde og fart for hvert segment; biler fordeles jevnt i **tid**, og får ulik romlig avstand når veifarten varierer.

`N_max` beregnes konservativt med rutens laveste fart `v_min`, minste fri følgeavstand `f` i sekunder og minste støtfangeravstand `d` i meter:

```text
minste tillatte front-til-front-tid = max(f + l/v_min, (l+d)/v_min)
N_max = floor(T / minste tillatte front-til-front-tid)
```

Hvis `N_min > N_max`, kan ingen jevnt fordelt bilstrøm samtidig blokkere lukene og oppfylle avstandskravene. Visualiseringen kan fortsatt vise det hypotetiske bilantallet, tydelig merket som uforenlig. Over 150 biler tegnes et representativt utvalg rundt hele løkken; beregningen bruker alltid hele antallet.

## Modellens grenser

- Forkjørsrett ved det valgte punktet og den vikepliktige manøveren bekreftes av brukeren. NVDB-data og automatisk skiltvalidering er ikke integrert.
- Lys, stopp og vikeplikt på løkken kan avbryte flyten. Kartlagte slike kontrollpunkter telles i resultatet, men ventetid simuleres ikke.
- OSM kan mangle eller inneholde feil adgang, svingeregler og fartsgrenser. Feltskifte, rundkjøringsfelt og alle norske trafikkregler er ikke modellert.
- Fart lik fartsgrense og momentane fartsskifter er idealiseringer. Akselerasjon, kurvefart, kø, reaksjonstid, motgående konflikter, endringer i gap acceptance og annen trafikk simuleres ikke.
- Dette er en matematisk utforsker, ikke en garanti for faktisk trafikkatferd eller et bekreftet lovlig trafikkopplegg. En SUMO-modell og NVDB-verifisering vil være egne videreutviklinger.

## Verifisering

```bash
python -m pytest -q
node --check app/static/app.js
python -m compileall -q app
```

Nettlesertester (Chromium):

```bash
pip install playwright
python -m playwright install chromium
BROWSER_TESTS=1 python -m pytest tests/test_browser.py -q
```

Eller `make check` og `make browser-test`. Nettlesertestene kjører faktisk frontend og API mot et **syntetisk OSM-formatert veinett**, uten eksterne nettjenester. De tester kartvalg, stedssøk, utkjøringsvalg, korteste/raskeste rute, gap-animasjon, pause, feil uten gamle resultater, mobilbredde og GeoJSON-eksport. GitHub Actions kjører både modell-/API-tester og nettlesertester. Live OSM- og Nominatim-tilgjengelighet må kontrolleres fra miljøet appen skal kjøre i.

## Prosjekt

```text
app/engine.py            Trafikkformler, svingekontroll og rutesøk
app/roads.py             Overpass, OSM-graf, områdebuffer og punktsnapping
app/main.py              FastAPI, stedssøk og kartøkter
app/static/              Leaflet-kart og trafikkanimasjon
app/static/vendor/       Leaflet 1.9.4 (BSD-2-Clause; lisens vedlagt)
tests/                   Modell-, API-, OSM- og nettlesertester
Makefile / Dockerfile    Lokal oppstart og container
.github/workflows/       Kontinuerlig verifisering
```

Kart og veidata: © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Stedssøk: Nominatim. Følg [kartflisvilkårene](https://operations.osmfoundation.org/policies/tiles/) og [Nominatim-vilkårene](https://operations.osmfoundation.org/policies/nominatim/) ved offentlig drift. Offentlige OSM-tjenester er egnet til småskala utforsking; større drift bør bruke egne eller avtalte tjenester.
