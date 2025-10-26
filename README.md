## Overview

The system combines hardware, software, and analytics to track live sound or visual data from a sensor (in our case, a Raspberry Pi camera), send it to a Express.js backend, display it in a web dashboard, and analyze historical trends.

### Main Components

- **`camera/`**  
  Contains the Python script that runs on a Raspberry Pi. It reads from a sound module and a camera, converts it into numerical form (e.g., sound levels), and sends live updates to the backend server.

- **`server/`**  
  Express.js backend that receives incoming data from the camera, stores it in a MongoDB database, and exposes REST API endpoints for the client to fetch.

- **`client/`**  
  React-based web dashboard for visualizing real-time and historical traffic data. It pulls from the backend and displays charts, counts, and other analytics.

- **`PredStats/`**  
  A Python-based predictive analytics module. It includes a CSV dataset (`PredStats.csv`) and a script (`PredStats.py`) used to test simple prediction models on simulated traffic data.