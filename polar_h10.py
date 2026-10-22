#!/usr/bin/env python3
"""
polar_h10.py
---------------------
Captura ECG e acelerômetro (ACC) brutos de uma cinta Polar H10 via Bluetooth
Low Energy, usando a biblioteca `bleakheart` (que implementa corretamente o
protocolo proprietário Polar Measurement Data - PMD - sobre a lib `bleak`).

Isso NÃO usa o app oficial da Polar nem precisa dele. Roda direto no seu
computador (Windows, macOS ou Linux) com Bluetooth.

IMPORTANTE: este script precisa de um adaptador Bluetooth real e da cinta
H10 fisicamente por perto (com eletrodos umedecidos e bom contato com a
pele). Ele não funciona dentro de uma sandbox/container sem Bluetooth.

Saída:
  Ao iniciar, o script pergunta o nome do paciente e a posição. Os arquivos
  vão para coletas/<PACIENTE>/ (ao lado deste script; a pasta do paciente é
  criada na primeira medição), com o nome
  PACIENTE_POSICAO_AAAA-MM-DD_hh-mm-ss_<tipo> (data/hora do início da gravação).
  Três arquivos CSV (ECG, ACC e HR/RR), cada amostra com timestamp individual
  em epoch (segundos, float) já convertido do relógio do sensor, e um JSON de
  metadados da sessão (taxas de amostragem, resolução, horário, device id).
  HR/RR vêm do serviço padrão de frequência cardíaca da cinta (batimento a
  batimento), independente do ECG bruto.

Uso:
    python3 polar_h10.py --duration 300                   # para sozinho em 300 s
    python3 polar_h10.py                                  # roda até apertar Enter
    python3 polar_h10.py --acc-rate 200                   # ACC em 200 Hz (padrão: 50 Hz)

Dependências: bleakheart, bleak  (pip install bleakheart bleak)
"""

import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleakheart import BatteryLevel, HeartRate, PolarMeasurementData

# no Windows, loop.add_reader não funciona em stdin -> usa thread para o Enter
if sys.platform == "win32":
    from threading import Thread

    ADD_READER_SUPPORT = False
else:
    ADD_READER_SUPPORT = True


def _default_on_event(name, **kw):
    """Callback padrão: imprime no terminal (comportamento de sempre da CLI)."""
    messages = {
        "scanning": "Procurando dispositivos BLE (aproxime-se do sensor)...",
        "not_found": "Nenhuma Polar H10 encontrada. Verifique se a cinta está "
                     "ligada (eletrodos úmidos), dentro do alcance e se não está "
                     "conectada a outro aparelho (ex. celular com o Polar Flow).",
        "found": f"Encontrado: {kw.get('device_name')} ({kw.get('device_address')})",
        "connected": f"Conectado: {kw.get('is_connected')}",
        "battery": f"Bateria do sensor: {kw.get('battery_percent')}%",
        "battery_error": f"(não consegui ler bateria: {kw.get('message')})",
        "measurements": f"Medições disponíveis no sensor: {kw.get('available')}",
        "settings": f"Configurações ECG disponíveis: {kw.get('ecg_settings')}\n"
                    f"Configurações ACC disponíveis: {kw.get('acc_settings')}",
        "recording": (f"Gravando ECG (130 Hz) + ACC ({kw.get('acc_rate')} Hz) + HR/RR... "
                      + (f"Vai parar sozinho em {kw.get('duration')}s, ou aperte Enter para parar antes."
                         if kw.get('duration') else ">>> Aperte Enter para encerrar a gravação <<<")),
        "duration_reached": "Duração atingida, encerrando.",
        "disconnected": "Sensor desconectou.",
        "error": f"Erro: {kw.get('message')}",
        "finished": (f"\nGravação finalizada ({kw.get('elapsed', 0):.1f}s).\n"
                     f"  ECG: {kw.get('n_ecg_samples')} amostras -> {kw.get('ecg_path')}\n"
                     f"  ACC: {kw.get('n_acc_samples')} amostras -> {kw.get('acc_path')}\n"
                     f"  HR/RR: {kw.get('n_hr_samples')} batimentos -> {kw.get('hr_path')}\n"
                     f"  Metadados -> {kw.get('meta_path')}"),
    }
    print(messages.get(name, f"{name}: {kw}"))


async def scan_for_h10(name_filter: str = "polar h10"):
    """Varre dispositivos BLE próximos procurando pela cinta Polar H10.

    O filtro precisa ser "polar h10" e não só "polar": o celular com o app
    Polar Flow também anuncia como "Polar mobile <id>" e seria pego por engano.
    """
    device = await BleakScanner.find_device_by_filter(
        lambda dev, adv: dev.name is not None and name_filter in dev.name.lower()
    )
    return device


def local_timestamp(epoch_s: float) -> str:
    """Converte epoch (segundos) para data/hora local legível, com milissegundos."""
    return datetime.fromtimestamp(epoch_s).isoformat(sep=" ", timespec="milliseconds")


def sanitize_filename_part(text: str) -> str:
    """Remove caracteres inválidos em nome de arquivo (Windows) e troca espaços por '-'."""
    invalid = '<>:"/\\|?*_'
    cleaned = "".join(c for c in text.strip() if c not in invalid and c.isprintable())
    return "-".join(cleaned.split())


def ask_non_empty(prompt: str) -> str:
    """Pergunta no terminal até receber um texto válido para nome de arquivo."""
    while True:
        value = sanitize_filename_part(input(prompt))
        if value:
            return value
        print("Valor vazio ou inválido, tente novamente.")


def expand_ecg_frame(tstamp_ns: int, samples, sample_rate: int):
    """
    bleakheart entrega um frame ECG como (tstamp_ns_do_ultimo_sample, [s1..sn]).
    Aqui reconstruímos o timestamp individual de cada amostra assumindo
    espaçamento uniforme 1/sample_rate, contando pra trás a partir do último.
    Retorna lista de tuplas (epoch_s, valor_uV).
    """
    n = len(samples)
    period = 1.0 / sample_rate
    t_last = tstamp_ns / 1e9
    t_first = t_last - (n - 1) * period
    return [(t_first + i * period, samples[i]) for i in range(n)]


def expand_acc_frame(tstamp_ns: int, samples, sample_rate: int):
    """Mesma ideia do ECG, mas para tuplas (x,y,z) em mili-g."""
    n = len(samples)
    period = 1.0 / sample_rate
    t_last = tstamp_ns / 1e9
    t_first = t_last - (n - 1) * period
    return [
        (t_first + i * period, samples[i][0], samples[i][1], samples[i][2])
        for i in range(n)
    ]


async def run_acquisition(outdir: Path, duration, acc_rate: int, acc_range: int,
                           on_event=None, patient: str = "paciente",
                           position: str = "posicao"):
    """Conecta na H10, grava ECG+ACC e escreve os CSVs/metadados.

    Os arquivos vão para <outdir>/<PACIENTE>/ e são nomeados
    PACIENTE_POSICAO_AAAA-MM-DD_hh-mm-ss_<tipo>, com a data/hora do início
    da gravação.

    `on_event(nome, **kwargs)` é chamado nos marcos importantes (scanning,
    found, connected, battery, measurements, settings, recording,
    duration_reached, disconnected, finished, error) para permitir integrar
    com outra interface (ex. servidor web) além dos prints da CLI. Em caso de
    falha, levanta RuntimeError em vez de sys.exit, para não matar o
    processo de quem estiver chamando esta função como biblioteca.
    """
    emit = on_event or _default_on_event

    emit("scanning")
    device = await scan_for_h10()
    if device is None:
        emit("not_found")
        raise RuntimeError("Nenhum sensor Polar encontrado.")
    emit("found", device_name=device.name, device_address=device.address)

    # uma pasta por paciente: todas as medições dele ficam juntas
    patient_dir = outdir / patient
    patient_dir.mkdir(parents=True, exist_ok=True)

    ecg_queue = asyncio.Queue()
    acc_queue = asyncio.Queue()
    hr_queue = asyncio.Queue()
    quit_event = asyncio.Event()

    def disconnected_callback(_client):
        emit("disconnected")
        quit_event.set()

    def keyboard_handler(loop=None):
        try:
            input()
        except EOFError:
            return
        if loop is None:
            quit_event.set()
        else:
            loop.call_soon_threadsafe(quit_event.set)

    ecg_rows = []
    acc_rows = []
    hr_rows = []

    async def ecg_consumer():
        while True:
            frame = await ecg_queue.get()
            if frame[0] == "QUIT":
                break
            _, tstamp_ns, samples = frame
            ecg_rows.extend(expand_ecg_frame(tstamp_ns, samples, ECG_SAMPLE_RATE))

    async def acc_consumer():
        while True:
            frame = await acc_queue.get()
            if frame[0] == "QUIT":
                break
            _, tstamp_ns, samples = frame
            acc_rows.extend(expand_acc_frame(tstamp_ns, samples, acc_rate))

    async def hr_consumer():
        while True:
            frame = await hr_queue.get()
            if frame[0] == "QUIT":
                break
            _, tstamp_ns, (hr_bpm, rr_ms), _energy = frame
            hr_rows.append((tstamp_ns / 1e9, hr_bpm, rr_ms))

    ECG_SAMPLE_RATE = 130  # fixo no H10, conforme especificação PMD

    # pair=True: a H10 exige pareamento (bonding) para liberar o serviço PMD
    # (ECG/ACC); sem isso o Windows responde "Insufficient Authentication".
    async with BleakClient(device, disconnected_callback=disconnected_callback,
                           pair=True) as client:
        emit("connected", is_connected=client.is_connected)

        battery = BatteryLevel(client)
        try:
            pct = await battery.read()
            emit("battery", battery_percent=pct)
        except Exception as e:
            pct = None
            emit("battery_error", message=str(e))

        pmd = PolarMeasurementData(client, ecg_queue=ecg_queue, acc_queue=acc_queue)
        # instant_rate=True: usa o intervalo RR individual pra calcular a FC
        # instantânea por batimento, em vez da média que o sensor manda.
        hr = HeartRate(client, queue=hr_queue, instant_rate=True, unpack=True)

        available = await pmd.available_measurements()
        emit("measurements", available=available)

        ecg_settings = await pmd.available_settings("ECG")
        acc_settings = await pmd.available_settings("ACC")
        emit("settings", ecg_settings=dict(ecg_settings), acc_settings=dict(acc_settings))

        loop = asyncio.get_running_loop()
        if ADD_READER_SUPPORT:
            loop.add_reader(sys.stdin, keyboard_handler)
        else:
            Thread(target=keyboard_handler, kwargs={"loop": loop}, daemon=True).start()

        err_code, err_msg, _ = await pmd.start_streaming("ECG")
        if err_code != 0:
            raise RuntimeError(f"Erro ao iniciar ECG: {err_msg}")

        err_code, err_msg, _ = await pmd.start_streaming(
            "ACC", SAMPLE_RATE=acc_rate, RANGE=acc_range
        )
        if err_code != 0:
            raise RuntimeError(f"Erro ao iniciar ACC: {err_msg}")

        await hr.start_notify()

        start_time = time.time()
        emit("recording", acc_rate=acc_rate, duration=duration)

        consumer_tasks = [
            asyncio.ensure_future(ecg_consumer()),
            asyncio.ensure_future(acc_consumer()),
            asyncio.ensure_future(hr_consumer()),
        ]

        if duration:
            try:
                await asyncio.wait_for(quit_event.wait(), timeout=duration)
            except asyncio.TimeoutError:
                emit("duration_reached")
        else:
            await quit_event.wait()

        elapsed = time.time() - start_time

        if client.is_connected:
            await pmd.stop_streaming("ECG")
            await pmd.stop_streaming("ACC")
            await hr.stop_notify()

        if ADD_READER_SUPPORT:
            try:
                loop.remove_reader(sys.stdin)
            except Exception:
                pass

        ecg_queue.put_nowait(("QUIT", None, None))
        acc_queue.put_nowait(("QUIT", None, None))
        hr_queue.put_nowait(("QUIT", None, None))
        await asyncio.gather(*consumer_tasks)

    # ':' não é permitido em nome de arquivo no Windows -> hora como hh-mm-ss
    start_str = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d_%H-%M-%S")
    prefix = f"{patient}_{position}_{start_str}"
    ecg_path = patient_dir / f"{prefix}_ecg.csv"
    acc_path = patient_dir / f"{prefix}_acc.csv"
    hr_path = patient_dir / f"{prefix}_hr.csv"
    meta_path = patient_dir / f"{prefix}_metadata.json"

    # --- grava CSVs ---
    # timestamp_local: data/hora legível (fuso do computador). timestamp_epoch_s:
    # continua junto para os cálculos numéricos (taxa de amostragem, janelas etc).
    with open(ecg_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_local", "timestamp_epoch_s", "ecg_uV"])
        w.writerows((local_timestamp(t), t, v) for t, v in ecg_rows)

    with open(acc_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_local", "timestamp_epoch_s", "x_mg", "y_mg", "z_mg"])
        w.writerows((local_timestamp(t), t, x, y, z) for t, x, y, z in acc_rows)

    with open(hr_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_local", "timestamp_epoch_s", "hr_bpm", "rr_ms"])
        w.writerows((local_timestamp(t), t, hr_bpm, rr_ms) for t, hr_bpm, rr_ms in hr_rows)

    metadata = {
        "patient": patient,
        "position": position,
        "device_name": device.name,
        "device_address": device.address,
        "battery_percent": pct,
        "ecg_sample_rate_hz": ECG_SAMPLE_RATE,
        "acc_sample_rate_hz": acc_rate,
        "acc_range_g": acc_range,
        "requested_duration_s": duration,
        "actual_duration_s": round(elapsed, 3),
        "n_ecg_samples": len(ecg_rows),
        "n_acc_samples": len(acc_rows),
        "n_hr_samples": len(hr_rows),
        "recorded_at_epoch_s": start_time,
        "recorded_at_local": local_timestamp(start_time),
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    emit("finished", elapsed=elapsed, n_ecg_samples=len(ecg_rows),
         n_acc_samples=len(acc_rows), n_hr_samples=len(hr_rows),
         ecg_path=str(ecg_path), acc_path=str(acc_path), hr_path=str(hr_path),
         meta_path=str(meta_path), metadata=metadata)

    return metadata


def main():
    parser = argparse.ArgumentParser(description="Captura ECG+ACC do Polar H10")
    parser.add_argument("--outdir", type=str,
                         default=str(Path(__file__).resolve().parent / "coletas"),
                         help="Pasta de saída para os CSVs e metadados "
                              "(padrão: 'coletas' ao lado deste script)")
    parser.add_argument("--duration", type=float, default=None,
                         help="Duração da gravação em segundos (padrão: até apertar Enter)")
    parser.add_argument("--acc-rate", type=int, default=50, choices=[25, 50, 100, 200],
                         help="Taxa de amostragem do acelerômetro em Hz (padrão: 50)")
    parser.add_argument("--acc-range", type=int, default=2, choices=[2, 4, 8],
                         help="Faixa dinâmica do acelerômetro em G (padrão: 2)")
    args = parser.parse_args()

    patient = ask_non_empty("Nome do paciente: ")
    position = ask_non_empty("Posição: ")

    try:
        asyncio.run(run_acquisition(Path(args.outdir), args.duration,
                                     args.acc_rate, args.acc_range,
                                     patient=patient, position=position))
    except RuntimeError as e:
        print(f"Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
