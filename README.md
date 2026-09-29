# CT-Defektanalyse

Lokaler, automatischer Prototyp: Zellflaechen transparent gruen, dunkle Defektkandidaten rot.
Es werden keine Noten vorhergesagt und keine Bilder extern uebertragen.

## Start

### Bildansicht mit Regler

```powershell
& '.\.venv\Scripts\python.exe' .\defect_viewer.py
```

Die Ansicht oeffnet standardmaessig alle PNG- und JPG-Bilder aus `Bilder_Processed_1`.
Ein anderer Bildordner kann als Argument angegeben werden.

- Mit `<`, `>` oder der Bildliste wechseln; die Reihenfolge folgt den Zellnummern.
- Schwellwert zwischen 1 und 254 einstellen. Die Anzeige wird nach kurzer Verzoegerung aktualisiert.
- Der Schwellwert bleibt beim Bildwechsel erhalten und veraendert nur die Defektkandidaten, nicht die Zellmaske.
- `Original` schaltet die farbige Ueberlagerung aus und wieder ein.
- `Exportieren` speichert Overlay, Masken und Messbericht in einem neuen Unterordner des gewaehlten Ziels.

### Querlinien und z-Pixel

Die Ansicht startet im Modus `Beides`: Zellflaechen und Defekte werden gruen/rot dargestellt,
darueber liegen die Querlinien. `Querlinien` und `Defekte` zeigen die Ergebnisse weiterhin einzeln.
Linienmarkierungen liegen an Kreuzungsstellen ueber den Defektfarben; die Messmasken bleiben unveraendert.
`Linienkontrast` filtert die Querlinienkandidaten unabhaengig vom `Defektkontrast`.
Beide Reglerwerte bleiben beim Bildwechsel erhalten. `Original` blendet alle Markierungen aus.

- Es werden ausschliesslich helle Querlinien gesucht und violett markiert.
- Die Erkennung dunkler Defektflecken bleibt unveraendert und wird weiterhin rot angezeigt.
- Die Tabelle zeigt Zellindex, z-Pixel, Linientyp und Profilprominenz (Linienkontrast).
- Die Zellindizes zaehlen die erkannten Zellen im Ausschnitt von links nach rechts, nicht die Excel-Zellnummern.
- Eine ausgewaehlte Tabellenzeile hebt das zugehoerige Segment gelb hervor.
- `z_px` ist die ganzzahlige Bildzeile: **0 am oberen Rand, nach unten zunehmend**.
- Die Koordinate gilt fuer das geladene Bild in Originalaufloesung, nicht fuer die skalierte Fensteranzeige.
- Sie ist keine kalibrierte CT-Raumkoordinate. Unterschiedliche Ausschnitte haben unterschiedliche Nullpunkte.

Die Berechnung entfernt langsame vertikale Helligkeitstrends und mittelt innerhalb ueberlappender Teilstreifen
von einem Viertel der erkannten Zellbreite. SciPy sucht lokale Helligkeitsmaxima mit Mindestabstand 6 Pixel
und Profilbreite zwischen 1 und 12 Pixeln. Kandidaten desselben Typs innerhalb von 3 Pixeln werden
pro Zelle zusammengefuehrt; die staerkste Position bleibt erhalten.
Bekannte Messlinien und Zellraender sind ausgeschlossen; ausreichend viele gueltige Pixel und ein
seitlich zusammenhaengendes Helligkeitssignal sind erforderlich.
Ein Vergleich im urspruenglichen Helligkeitsprofil verlangt zudem einen helleren Wert als oberhalb und
unterhalb des Kandidaten, um kuenstliche helle Saeume neben dunklen Linien zu unterdruecken.

Die gezeichneten Segmente und `x_start_px`/`x_end_px` kennzeichnen den ausgewaehlten Profilstreifen,
**nicht die nachgewiesenen Endpunkte der Querlinie**. Die z-Position ist dessen helles Profilmaximum.
Geneigte, sehr kurze, breite oder kontrastarme Linien koennen fehlen oder mehrfach erkannt werden.
Komplexe Helligkeitsuebergaenge koennen weiterhin Fehlkandidaten liefern. Es wird nicht auf Periodizitaet gefiltert.
Falsche Zellmasken und nicht erkannte Messgrafik beeinflussen die Ergebnisse weiterhin.

Im Export entstehen zusaetzlich:

- `horizontal_lines.csv`: Semikolon-getrennte Koordinatentabelle mit UTF-8-Kodierung fuer Excel.
- `horizontal_lines.png`: Querlinien auf dem unveraenderten Originalhintergrund.
- `measurements.json`: zusaetzlich Linienliste, Linienkontrast und Definition des Koordinatensystems.

Der bestehende Einzelbild-Aufruf von `detect_defects.py` wertet nur Defektkandidaten aus;
die Querlinienfunktion ist in der Bildansicht verfuegbar.

Hoehere Werte verlangen mehr lokalen Dunkelkontrast: Bei gleichem Bild koennen rote Pixel nur verschwinden.
Bei niedrigeren Werten koennen rote Pixel nur hinzukommen. Die Aenderung ist nicht zwingend gleichmaessig;
Graustufen sind diskret und sehr kleine Regionen werden erst ab einer Mindestgroesse angezeigt.
Die Anzahl einzelner Kandidaten kann beim Verbinden oder Trennen von Regionen trotzdem steigen oder sinken.
Eine neue Analyse deaktiviert den Export bis das zum Bild und Regler passende Ergebnis vorliegt.
Nicht auswertbare Bilder zeigen einen Fehlerstatus; die Navigation bleibt verfuegbar.
Bildvorbereitung wird fuer bis zu drei Bilder zwischengespeichert. Es werden keine Originale veraendert.

### Einzelbild ohne Fenster

In PowerShell im Projektordner:

```powershell
& '.\.venv\Scripts\python.exe' .\detect_defects.py 'Bilder_Processed_1\CT9182_BBK_A-Muster_Halbmodul_KlapperStack_Schublade 1 Zelle 10.png' --output 'Meine_Defektanalyse'
```

Der Standardlauf erkennt Zellflaechen und lokale dunkle Kandidaten ohne Rahmen und ohne Klicks.
Anschliessend `overlay.png` im Ausgabeordner oeffnen.

Nur bei Bedarf `--manual` ergaenzen: Zellen einrahmen, jeweils Enter druecken, dann Esc;
anschliessend dunkle Defektpunkte anklicken und mit Enter bestaetigen.

Der Ausgabeordner muss neu sein. Originalbilder bleiben unveraendert.
Nur der optionale manuelle Modus benoetigt eine grafische Windows-Sitzung.
Die automatische Auswertung ist technisch getestet, aber fachlich noch nicht validiert.

## Ergebnisse

- `overlay.png`: gruen-rote Darstellung auf dem Original.
- `cells_mask.png`: vereinigte Zellmaske, weiss innerhalb der Zellflaechen.
- `defects_mask.png`: Kandidatenmaske, weiss innerhalb der gefundenen dunklen Regionen.
- `excluded_mask.png`: ausgeschlossene Messlinien und farbige Markierungen.
- `measurements.json`: Parameter, Zellzuordnung, Flaeche und Laenge/Breite je Defekt.

Das automatisch berechnete Beispiel liegt in `Defektanalyse_Automatisch`.
Die frueheren halbautomatischen Versuche liegen in `Defektanalyse_Beispiel` und `Defektanalyse_Beispiel_2`.
Die Parameter zur Wiederholung stehen im jeweiligen Messbericht.

## Parameter

- `--manual`: interaktive Auswahl statt automatischer Erkennung.
- `--cell LINKS OBEN BREITE HOEHE`: Zellrechteck in Originalpixeln, mehrfach moeglich.
- `--point X Y`: Punkt im dunklen Fleck, mehrfach moeglich; zusammen mit `--cell` angeben.
- `--scale 81`: ungerade Suchgroesse fuer dunkle Strukturen; groesser als die gesuchte Fleckbreite waehlen.
- `--threshold 8`: lokaler Mindestkontrast; niedrigere Werte koennen groessere Masken und Fehlalarme erzeugen.
- `--ignore-line X1 Y1 X2 Y2 BREITE`: schwarz-weisse Messhilfslinie ausschliessen, mehrfach moeglich.
- `--pixel-size-mm WERT`: nur eine fachlich bestaetigte isotrope Pixelkalibrierung verwenden.

## Grenzen

Die automatische Zellmaske verwendet Helligkeitsschwellen und Formfilter fuer aufrechte CT-Zellansichten.
Eingaben sind die CT-Ausschnitte, nicht vollstaendige Bildschirmaufnahmen mit weiteren Ansichten.
Im Beispiel werden weiterhin Zwischenraeume teilweise als Zellflaeche und periodische CT-Strukturen als
Defektkandidaten erkannt. Zellenden koennen fehlen. Der manuelle Modus verwendet weiterhin GrabCut.
Zellflaeche und Defektanteil sind deshalb vorlaeufig, keine freigegebenen Messwerte.

Die automatische Suche liefert zusammenhaengende lokale dunkle Regionen innerhalb der Zellmasken.
Sehr kleine, sehr schmale und randnahe Regionen werden gefiltert; entsprechende echte Defekte koennen fehlen.
Grosse oder laengliche Regionen werden nicht mehr nachtraeglich ausgeblendet, damit der Schwellwert
keine bereits roten Bereiche durch das Zusammenwachsen von Regionen verschwinden laesst.
Dadurch bleiben auch grosse Fehlkandidaten sichtbar und muessen fachlich geprueft werden.
Es handelt sich nicht um ein trainiertes Defektmodell. Unmarkierte Bereiche sind kein Nachweis fuer Fehlerfreiheit.
Die Kontur haengt von Kontrastschwelle, Suchgroesse und CT-Darstellung ab und muss fachlich bestaetigt werden.
Schraege helle Messlinien werden automatisch gesucht; die Erkennung ist unvollstaendig und kann Bildstruktur treffen.
Mit `--ignore-line` koennen zusaetzliche Messlinien ausgeschlossen werden.
Farbige Markierungen werden fuer die Segmentierung interpoliert, aber nicht als Defektflaeche vermessen.
Verdeckte Bildinformationen werden dadurch nicht wiederhergestellt.

Flaechen sind Pixelanzahlen; Laenge und Breite sind die Seiten eines gedrehten Begrenzungsrechtecks.
Ohne Kalibrierung erfolgen keine Angaben in mm oder mm2. Aus einem Schnitt wird kein Volumen bestimmt.
Vor automatischer Stapelverarbeitung sind fachlich annotierte Masken und unabhaengige Testbilder erforderlich.

## Tests

```powershell
& '.\.venv\Scripts\python.exe' -m unittest test_detect_defects -v
```

Die synthetischen Tests pruefen automatische Zell- und Kandidatensuche ohne Klicks, Faelle ohne Kandidaten,
dunkle gegen helle Flecken, Ausschlussmasken, Farben und Pixelmessungen.
Ein Regressionstest prueft das Zusammenwachsen zweier Flecken ueber alle 254 Reglerwerte:
Ein niedrigerer Schwellwert darf keine zuvor roten Pixel entfernen.
Ein Fenstertest prueft ausserdem Regler, Navigation, Fehlerzustaende, Originalansicht und Export.
Dieser Test benoetigt eine grafische Windows-Sitzung mit Tkinter.
Querlinientests pruefen bekannte Pixelhoehen, kurze und helle Linien sowie ausgeschlossene Messlinien.
Der Fenstertest prueft auch Linientabelle, getrennte Regler und CSV/JSON-Export.
Sie ersetzen keine Validierung gegen fachlich annotierte CT-Bilder.