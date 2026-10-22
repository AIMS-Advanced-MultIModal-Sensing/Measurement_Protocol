# Coleta de dados cardíacos e respiratórios – Polar H10

Scripts em Python para gravar e organizar dados da cinta **Polar H10**.
Neste script são coletados dados do acelerômetro.

## Requisitos

- Python 3.10 ou mais recente
- Computador com Bluetooth (Windows, macOS ou Linux)
- Dependências:

```bash
pip install bleak bleakheart
```

## 1. `polar_h10.py`: gravação direto da cinta

Grava **ECG bruto (130 Hz)**, **acelerômetro** e **FC/intervalos RR** pelo Bluetooth, sem usar o app da Polar.

### Antes de gravar

1. Molhe os eletrodos e coloque a cinta no peito. Ela só liga com contato na pele.
2. **Feche o app Polar Flow no celular** ou desligue o Bluetooth dele. Se o celular estiver conectado, o computador pode não encontrar a cinta.

### Uso

```bash
python polar_h10.py                   # grava até apertar Enter
python polar_h10.py --duration 150    # para sozinho depois de 150 s
```

O script pergunta o **nome do paciente** e a **posição**, e grava os arquivos em:

```
coletas/
└── <Paciente>/
    ├── <Paciente>_<Posicao>_<AAAA-MM-DD_hh-mm-ss>_ecg.csv
    ├── <Paciente>_<Posicao>_<AAAA-MM-DD_hh-mm-ss>_acc.csv
    ├── <Paciente>_<Posicao>_<AAAA-MM-DD_hh-mm-ss>_hr.csv
    └── <Paciente>_<Posicao>_<AAAA-MM-DD_hh-mm-ss>_metadata.json
```

- A pasta `coletas` fica **na mesma pasta do script**, em qualquer computador, e é criada automaticamente.
- A data e a hora são as do **início da gravação**.
- Os arquivos só são salvos quando a gravação termina, pela duração ou pelo Enter. Se o script for fechado com Ctrl+C, os dados se perdem.
- Digite o nome do paciente sempre igual.

### Opções

| Opção | Padrão | Descrição |
|---|---|---|
| `--duration` | até Enter | Duração em segundos |
| `--acc-rate` | 50 | Taxa do acelerômetro (25, 50, 100 ou 200 Hz) |
| `--acc-range` | 2 | Faixa do acelerômetro (2, 4 ou 8 G) |
| `--outdir` | `coletas/` | Pasta de saída alternativa |

### Colunas dos CSVs

| Arquivo | Colunas |
|---|---|
| `ecg.csv` | `timestamp_local`, `timestamp_epoch_s`, `ecg_uV` |
| `acc.csv` | `timestamp_local`, `timestamp_epoch_s`, `x_mg`, `y_mg`, `z_mg` |
| `hr.csv` | `timestamp_local`, `timestamp_epoch_s`, `hr_bpm`, `rr_ms` |

# Coleta de dados cardíacos e respiratórios – Hexoskin

Site para cadastro:

https://api.hexoskin.com/api/connect/login/?next=%2Foauth2%2Fauthorize%2F%3Fresponse_type%3Dtoken%26state%3D17926759343262456%26client_id%3DT0IMLTqekAin4vbY2%26redirect_uri%3Dhttps%253A%252F%252Fmy.hexoskin.com%252F


