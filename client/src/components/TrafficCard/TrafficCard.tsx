import TrafficIndicator from "../TrafficIndicator/TrafficIndicator.tsx";
import "./TrafficCard.css"
interface TrafficCardProps {
  name: string;
  traffic: number;
  soundLevel: number;
  onClick: () => void;
}

const DiningCard = ({ name, traffic, soundLevel, onClick }: TrafficCardProps) => {
  return (
    <div className="dining-card">
      <h2 className="card-title">{name}</h2>
      <TrafficIndicator level={traffic < 5 ? "light" : (traffic < 10 ? "moderate" : "busy")} />
      <p className="wait-time">Current Occupancy: {traffic}</p>
      <p className="sound-level">Sound Level: {soundLevel}</p>
    </div>
  );
}

export default DiningCard;