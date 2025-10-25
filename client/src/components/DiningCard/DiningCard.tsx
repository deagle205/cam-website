import TrafficIndicator from "../TrafficIndicator/TrafficIndicator.tsx";
import "./DiningCard.css"
interface DiningCardProps {
  name: string;
  traffic: string;
  occupancy: number;
}

const DiningCard = ({ name, traffic, occupancy }: DiningCardProps) => {
  return (
    <div className="dining-card">
      <h2 className="card-title">{name}</h2>
      <TrafficIndicator level={traffic} />
      <p className="wait-time">Current Occupancy: {occupancy}</p>
    </div>
  );
}

export default DiningCard;