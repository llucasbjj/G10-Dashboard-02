import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from geopy.geocoders import Nominatim
import time
import requests
import os

# --- SETUP DA PÁGINA ---
st.set_page_config(page_title="G10 Abastecimentos", layout="wide", page_icon="⛽")

# --- CABEÇALHO ---
col_logo, col_title = st.columns([1, 4])
with col_logo:
    logo_path = 'g10-image.png'
    if os.path.exists(logo_path):
        st.image(logo_path, width=150)
with col_title:
    st.title("Hub de Abastecimento G10")
    st.markdown("Monitoramento georreferenciado e fluxo de paradas da frota.")

# --- CARREGAR DADOS ---
@st.cache_data
def load_data():
    # O usuário consolidou tudo em uma única planilha
    try:
        df = pd.read_excel('postos_filtrados_planilha.xlsx')
        # Renomeia colunas caso tenham espaços extras ou nomes diferentes
        df.rename(columns={'Posto ': 'Posto Abastecido', 'posto': 'Posto Abastecido'}, inplace=True)
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        return pd.DataFrame()
    
    return df

df = load_data()

# --- GEOLOCALIZAÇÃO (SIMPLES) ---
# Vamos buscar as coordenadas com base no Município e UF
@st.cache_data
def get_coordinates(df):
    geolocator = Nominatim(user_agent="gas_station_dashboard")
    
    # Criar uma lista única de cidades para não consultar a mesma cidade várias vezes
    cidades = df[['Municipio', 'UF']].drop_duplicates().dropna()
    coords = {}
    
    # Adicionando um placeholder pro progresso
    with st.spinner('Aprimorando dados geográficos (isso só acontece na primeira vez)...'):
        for index, row in cidades.iterrows():
            cidade_uf = f"{row['Municipio']}, {row['UF']}, Brasil"
            try:
                location = geolocator.geocode(cidade_uf, timeout=5)
                if location:
                    coords[cidade_uf] = (location.latitude, location.longitude)
            except:
                coords[cidade_uf] = (None, None)
            time.sleep(1) # Respeitando o limite do geopy
            
    # Mapeando de volta para o DataFrame
    df['Lat'] = df.apply(lambda x: coords.get(f"{x['Municipio']}, {x['UF']}, Brasil", (None, None))[0] if pd.notnull(x['Municipio']) else None, axis=1)
    df['Lon'] = df.apply(lambda x: coords.get(f"{x['Municipio']}, {x['UF']}, Brasil", (None, None))[1] if pd.notnull(x['Municipio']) else None, axis=1)
    
    return df

df_mapa = get_coordinates(df.copy())
df_mapa = df_mapa.dropna(subset=['Lat', 'Lon']) # Removemos quem não achou no mapa


if df.empty:
    st.error("Erro ao carregar dados.")
    st.stop()


# --- KPIs (MÉTRICAS PRINCIPAIS) ---
st.subheader("📊 Resumo Geral")
col1, col2 = st.columns(2)

total_abastecimentos = df['Abastecimentos'].sum()

# Considerando que Último valor pago é um valor representativo ou que precisamos calcular um gasto total (vamos simplificar)
# Supondo que a coluna esteja no formato de string contendo R$ ou numerico direto.
try:
    df['Custo_Aproximado'] = pd.to_numeric(df['ltimo valor Pago'].astype(str).str.replace(r'R\$\s?', '', regex=True).str.replace('.', '', regex=False).str.replace(',', '.', regex=False), errors='coerce')
    total_gasto = df['Custo_Aproximado'].sum()
except:
    total_gasto = 0

col1.metric("Qtde. Total Abastecimentos", f"{total_abastecimentos}")
col2.metric("Postos Cadastrados", f"{len(df['Posto Abastecido'].unique())}")

st.divider()

@st.cache_data
def get_route(coords):
    if len(coords) < 2: return None
    coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data['code'] == 'Ok' and len(data['routes']) > 0:
                return data['routes'][0]['geometry']['coordinates']
    except Exception as e:
        print("Erro ao buscar rota:", e)
    return None

# --- MAPA DE CALOR ---
st.subheader("🗺️ Mapa de Densidade de Abastecimento e Rotas")
st.markdown("O mapa exibe a localização dos postos e a intensidade da cor indica a quantidade de abastecimentos na região. A linha azul simula a rota otimizada entre eles pelas rodovias principais.")

if not df_mapa.empty:
    # Verifica qual métrica usar para o tamanho
    # Se abastecimentos não for int, converte
    df_mapa['Abastecimentos'] = pd.to_numeric(df_mapa['Abastecimentos'], errors='coerce').fillna(1)
    
    fig_map = go.Figure()

    # 1. Pegamos os top 20 postos mais frequentes para traçar a rota (limite da API do OSRM)
    coords_df = df_mapa.sort_values(by='Abastecimentos', ascending=False)[['Lon', 'Lat']].dropna().drop_duplicates().head(20)
    coords_list = list(zip(coords_df['Lon'].tolist(), coords_df['Lat'].tolist()))

    route_coords = get_route(coords_list)
    if route_coords:
        route_lon = [c[0] for c in route_coords]
        route_lat = [c[1] for c in route_coords]
        
        # Adiciona a Rota PRIMEIRO (Fica por baixo do Heatmap)
        fig_map.add_trace(go.Scattermapbox(
            mode="lines",
            lon=route_lon,
            lat=route_lat,
            line=dict(width=1.5, color="rgba(255, 165, 0, 0.4)"), # Laranja com alta transparência
            name="Principal Rota Viária",
            hoverinfo="skip"
        ))

    # 2. Adiciona os 'Pontos de Brilho' (Densidade das paradas com mapa termal)
    fig_map.add_trace(go.Densitymapbox(
        lat=df_mapa['Lat'],
        lon=df_mapa['Lon'],
        z=df_mapa['Abastecimentos'], # A quantidade define o "calor" (intensidade)
        radius=45, # Aumentou o halo para brilho beem maior
        colorscale="Inferno", # cores ressaltam no fundo escuro
        hovertext=df_mapa['Posto Abastecido'],
        hoverinfo="text",
        showscale=False
    ))

    # 3. Adiciona os nomes dos Municípios que possuem postos sobre o mapa e pontos brancos para marcar o centro
    fig_map.add_trace(go.Scattermapbox(
        mode="markers+text",
        lon=df_mapa['Lon'],
        lat=df_mapa['Lat'],
        text=df_mapa['Municipio'],
        textposition="bottom right",
        textfont=dict(color="rgba(255, 255, 255, 0.9)", size=13, weight="bold"),
        marker=dict(size=8, color="white", opacity=1), # Ponto branco no meio do brilho muito mais visível
        hovertext=df_mapa['Posto Abastecido'],
        hoverinfo="text",
        showlegend=False
    ))
    
    # 4. Configura o layout (Mapbox escuro exibe apenas divisas e o que desenhamos)
    fig_map.update_layout(
        mapbox_style="carto-darkmatter",
        mapbox=dict(
            center=dict(lat=df_mapa['Lat'].mean(), lon=df_mapa['Lon'].mean()), 
            zoom=4
        ),
        margin={"r":0,"t":40,"l":0,"b":0},
        title="Rotas e Frequência de Paradas (Mapa de Brilho)",
        showlegend=False
    )
    st.plotly_chart(fig_map, use_container_width=True)
else:
    st.warning("Não foi possível gerar as coordenadas para o mapa automático no momento.")

st.divider()

st.divider()

# --- GRÁFICOS ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("🏆 Top 10 Postos (Por Qtde. Abastecimentos)")
    df_grouped = df.groupby('Posto Abastecido', as_index=False)['Abastecimentos'].sum()
    df_top10 = df_grouped.sort_values(by='Abastecimentos', ascending=False).head(10)

    fig_bar = px.bar(
        df_top10, 
        x='Abastecimentos', 
        y='Posto Abastecido', 
        orientation='h', 
        color='Abastecimentos',
        color_continuous_scale='Blues',
        text='Abastecimentos'
    )
    fig_bar.update_layout(yaxis={'categoryorder':'total ascending'}, showlegend=False, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig_bar, use_container_width=True)

with col2:
    st.subheader("📍 Abastecimentos por Estado (UF)")
    df_uf = df.groupby('UF', as_index=False)['Abastecimentos'].sum()
    fig_pie = px.pie(
        df_uf, 
        names='UF', 
        values='Abastecimentos', 
        hole=0.4,
        color_discrete_sequence=px.colors.qualitative.Pastel
    )
    fig_pie.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig_pie, use_container_width=True)

st.divider()

# --- TABELA INTERATIVA ---
st.subheader("📑 Relação Detalhada de Postos")
# As colunas disponíveis são: 'Posto Abastecido', 'CNPJ', 'Contato', 'Municipio', 'UF', 'Produto', 'Abastecimentos'
st.dataframe(df[['Posto Abastecido', 'Municipio', 'UF', 'Produto', 'Abastecimentos', 'CNPJ', 'Contato']], use_container_width=True)
