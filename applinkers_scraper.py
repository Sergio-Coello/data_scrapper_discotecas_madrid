
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


URL = "https://applinkers.com/entradas/madrid/calendario/"
SALAS_SEPARADAS = ("fitz", "vandido", "saint-club", "joy-eslava")
COLUMNAS = [
    "fecha", "nombre_evento", "discoteca", "ciudad", "hora",
    "precio_desde_eur", "condicion_lista_gratis", "edad_minima",
    "generos",
]
SALIDA = Path(__file__).resolve().parent / "datos_csv"


def hacer_clic(espera, selector):
    espera.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector))).click()


def aceptar_cookies(driver, espera):
    aviso = driver.find_elements(By.ID, "entCookieBanner")
    if aviso and aviso[0].is_displayed():
        hacer_clic(espera, "#entCookieAccept")
        espera.until(EC.invisibility_of_element_located((By.ID, "entCookieBanner")))


def obtener_salas_y_fechas(driver, espera):
    hacer_clic(espera, "[data-filtro-sala]")
    espera.until(EC.visibility_of_element_located((By.ID, "entSalasHoja")))
    salas = {
        enlace.get_attribute("data-sala"): enlace.get_attribute("data-nombre")
        for enlace in driver.find_elements(By.CSS_SELECTOR, "#entSalasHoja [data-sala]")
        if enlace.get_attribute("data-sala")
    }
    hacer_clic(espera, "#entSalasHoja [data-cerrar]")

    calendario = json.loads(
        driver.find_element(By.ID, "entCalDatos").get_property("textContent")
    )
    por_sala = {}
    for fecha, sala, _ in calendario["ev"]:
        date.fromisoformat(fecha)
        por_sala.setdefault(sala, Counter())[fecha] += 1
    return salas, por_sala


def mostrar_mes(driver, espera, mes_objetivo):
    actual = espera.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, "#entCal .ent-cal-mes:not([hidden])")
    )).get_attribute("data-mes")
    while actual != mes_objetivo:
        direccion = 1 if actual < mes_objetivo else -1
        anterior = actual
        hacer_clic(espera, f'#entCal button[data-cal-mover="{direccion}"]')
        espera.until(lambda d: d.find_element(
            By.CSS_SELECTOR, "#entCal .ent-cal-mes:not([hidden])"
        ).get_attribute("data-mes") != anterior)
        actual = driver.find_element(
            By.CSS_SELECTOR, "#entCal .ent-cal-mes:not([hidden])"
        ).get_attribute("data-mes")


def cargar_todas_las_fiestas(driver, espera):
    boton = "#entAgendaMas"
    while driver.find_element(By.CSS_SELECTOR, boton).is_displayed():
        anteriores = len(driver.find_elements(By.CSS_SELECTOR, "#entAgenda .ent-ev-card"))
        hacer_clic(espera, boton)
        espera.until(lambda d: len(d.find_elements(
            By.CSS_SELECTOR, "#entAgenda .ent-ev-card"
        )) > anteriores)


def texto(elemento, selector, atributo=None):
    campos = elemento.find_elements(By.CSS_SELECTOR, selector)
    if not campos:
        return ""
    valor = campos[0].get_attribute(atributo) if atributo else campos[0].text
    return (valor or "").strip()


def extraer_evento(tarjeta, fecha):
    fecha_web = texto(tarjeta, ".ent-ev-fecha")
    precio_web = texto(tarjeta, ".ent-ev-badge.is-precio")
    precio = re.search(r"(\d+(?:[.,]\d+)?)\s*€", precio_web)
    hora = re.search(r"\b\d{1,2}:\d{2}\b", fecha_web)
    gratis = tarjeta.find_elements(By.CSS_SELECTOR, ".ent-ev-badge.is-gratis")
    etiquetas = tarjeta.find_elements(
        By.CSS_SELECTOR, ".ent-ev-badge:not(.is-gratis,.is-edad,.is-precio)"
    )

    generos = []
    for etiqueta in etiquetas:
        nombre = etiqueta.text.strip()
        if nombre and not re.match(r"(?i)^a la venta\b", nombre) and nombre.casefold() != "agotado":
            generos.append(nombre)

    edad = texto(tarjeta, ".ent-ev-badge.is-edad").lstrip("+")
    return {
        "_sala": urlparse(texto(tarjeta, "a.ent-ev-club", "href")).path.rstrip("/").split("/")[-1],
        "fecha": fecha,
        "nombre_evento": texto(tarjeta, ".ent-ev-nombre"),
        "discoteca": texto(tarjeta, ".ent-ev-club"),
        "ciudad": "Madrid",
        "hora": hora.group().zfill(5) if hora else "",
        "precio_desde_eur": precio.group(1).replace(",", ".") if precio else "",
        "condicion_lista_gratis": " | ".join(e.text.strip() for e in gratis),
        "edad_minima": edad,
        "generos": " | ".join(generos),
    }


def recoger_eventos(driver, espera, salas, por_sala):
    eventos = []
    for sala, nombre in salas.items():
        if sala not in por_sala:
            continue
        hacer_clic(espera, "[data-filtro-sala]")
        espera.until(EC.visibility_of_element_located((By.ID, "entSalasHoja")))
        hacer_clic(espera, f'#entSalasHoja [data-sala="{sala}"]')
        espera.until(EC.invisibility_of_element_located((By.ID, "entSalasHoja")))

        for fecha, anunciados in sorted(por_sala[sala].items()):
            actual = parse_qs(urlparse(driver.current_url).query)
            if actual.get("dia", [""])[0] != fecha:
                mostrar_mes(driver, espera, fecha[:7])
                hacer_clic(espera, f'#entCal a[data-dia="{fecha}"]')
            espera.until(lambda d: (
                parse_qs(urlparse(d.current_url).query).get("sala") == [sala]
                and parse_qs(urlparse(d.current_url).query).get("dia") == [fecha]
                and d.find_element(By.ID, "entAgenda").get_attribute("aria-busy") != "true"
            ))
            cargar_todas_las_fiestas(driver, espera)
            tarjetas = driver.find_elements(By.CSS_SELECTOR, "#entAgenda .ent-ev-card")
            if len(tarjetas) != anunciados:
                raise ValueError(f"{nombre}, {fecha}: esperaba {anunciados}, encontré {len(tarjetas)}")
            eventos.extend(extraer_evento(tarjeta, fecha) for tarjeta in tarjetas)
        print(f"{nombre}: {sum(por_sala[sala].values())} eventos")
    return eventos


def guardar_csv(eventos):
    SALIDA.mkdir(exist_ok=True)
    grupos = {sala: [] for sala in SALAS_SEPARADAS}
    grupos["otras-discotecas"] = []
    for evento in eventos:
        sala = evento["_sala"]
        grupo = sala if sala in SALAS_SEPARADAS else "otras-discotecas"
        grupos[grupo].append(evento)

    for nombre, filas in grupos.items():
        filas.sort(key=lambda fila: (fila["fecha"], fila["hora"], fila["nombre_evento"]))
        with (SALIDA / f"{nombre}.csv").open("w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)
            escritor.writeheader()
            escritor.writerows({columna: fila[columna] for columna in COLUMNAS} for fila in filas)
        print(f"Guardado: {nombre}.csv ({len(filas)} eventos)")


def main():
    driver = webdriver.Chrome()
    espera = WebDriverWait(driver, 20)
    try:
        driver.get(URL)
        espera.until(EC.visibility_of_element_located((By.ID, "entCal")))
        aceptar_cookies(driver, espera)
        salas, por_sala = obtener_salas_y_fechas(driver, espera)
        eventos = recoger_eventos(driver, espera, salas, por_sala)
        guardar_csv(eventos)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
