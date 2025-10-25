import "./Home.css";
import DiningCard from "../../components/DiningCard/DiningCard.tsx";

const Home = () => {
  const diningHalls = [
    { name: "Traditions at Scott", traffic: "Moderate", occupancy: 197 },
    { name: "Traditions at Kennedy", traffic: "Busy", occupancy: 235 },
    { name: "Traditions at Morrill", traffic: "Light", occupancy: 56 },
  ];

  return (
    <div className="home">
      <header className="home-header">
        <h1>Campus Dining Traffic</h1>
        <p>Check current crowd levels across dining halls</p>
      </header>

      <main className="home-main">
        <div className="dining-grid">
          {diningHalls.map((hall) => (
            <DiningCard
              key={hall.name}
              name={hall.name}
              traffic={hall.traffic}
              occupancy={hall.occupancy}
            />
          ))}
        </div>
      </main>

      <footer className="home-footer">
        <p>Data is estimated. Updated every 5 minutes.</p>
      </footer>
    </div>
  );
}

export default Home;