import pandas as pd
from prophet import Prophet
import matplotlib.pyplot as plt
from prophet.plot import add_changepoints_to_plot
import seaborn as sns
from prophet.serialize import model_to_json, model_from_json
import plotly.graph_objects as go

df = pd.read_csv('client/src/PredStats/PredStats.csv')

df['ds'] = pd.to_datetime(df['ds'])

m = Prophet()
m.fit(df)

future = m.make_future_dataframe(periods=48, freq='H')

# Generate the forecast
forecast = m.predict(future)

with open('forecast_model.json', 'w') as fout:
    fout.write(model_to_json(m))


print("Model trained and saved to forecast_model.json")

plt.figure(figsize=(12, 6))
plt.style.use('ggplot')
sns.set_style('darkgrid')
plt.title("Future Congestion of Scott Tradition")
sns.lineplot(data=df, x='ds', y='y', palette="black")
plt.xlabel("Time in Hours")
plt.ylabel("People Counted")

plt.show()
