import "./Home.css";
import TrafficCard from "../../components/TrafficCard/TrafficCard.tsx";
import { useState, useEffect } from "react";

interface TrafficEntry {
  _id: string;
  buildingId: string;
  count: number;
  soundLevel: number;
  timestamp: string;
}

interface BuildingTraffic {
  buildingId: string;
  latest: TrafficEntry;
  history: TrafficEntry[];
}

const Home = () => {
  const [trafficData, setTrafficData] = useState<TrafficEntry[]>([]);
  const [buildings, setBuildings] = useState<BuildingTraffic[]>([]);
  const [selectedBuilding, setSelectedBuilding] =
    useState<BuildingTraffic | null>(null);

  const API_URL = import.meta.env.VITE_API_URL!;
  const API_KEY = import.meta.env.VITE_API_KEY!;

  // Fetch traffic entries from server
  useEffect(() => {
    const fetchTraffic = async () => {
      try {
        const res = await fetch(API_URL, {
          headers: { "x-api-key": API_KEY },
        });
        const data = await res.json();

        setTrafficData(data);
      } catch (err) {
        console.error("Error fetching traffic:", err);
      }
    };
    fetchTraffic();
  }, [API_URL, API_KEY]);

  // Process traffic: group by building, sort by timestamp
  useEffect(() => {
    const grouped: Record<string, TrafficEntry[]> = {};

    trafficData.forEach((entry) => {
      if (!grouped[entry.buildingId]) grouped[entry.buildingId] = [];
      grouped[entry.buildingId].push(entry);
    });

    const processed: BuildingTraffic[] = Object.entries(grouped).map(
      ([buildingId, entries]) => {
        const sorted = entries.sort(
          (a, b) =>
            new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
        );
        return {
          buildingId,
          latest: sorted[0],
          history: sorted.slice(0, 50),
        };
      }
    );

    setBuildings(processed);
  }, [trafficData]);

  return (
    <div className="home">
      <header className="home-header">
        <h1>Campus Traffic</h1>
      </header>

      <main className="home-main">
        <div className="traffic-grid">
          {buildings.map((building) => (
            <TrafficCard
              key={building.buildingId}
              name={building.buildingId}
              traffic={building.latest.count}
              soundLevel={building.latest.soundLevel}
              onClick={() => setSelectedBuilding(building)}
            />
          ))}
        </div>

        {selectedBuilding && (
          <>
            <div
              className="history-overlay"
              onClick={() => setSelectedBuilding(null)}
            />
            <div className="history-panel">
              <h2>History for {selectedBuilding.buildingId}</h2>
              <ul>
                {selectedBuilding.history.map((entry) => (
                  <li key={entry._id}>
                    {new Date(entry.timestamp).toLocaleString()}: {entry.count}
                    {entry.count != 1 ? " people, " : " person, "}
                    {entry.soundLevel}
                    {" db"}
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </main>
    </div>
  );
};

export default Home;
