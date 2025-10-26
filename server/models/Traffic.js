import mongoose from "mongoose";

const TrafficSchema = new mongoose.Schema({
  buildingId: String,
  count: Number,
  soundLevel: Number,
  timestamp: { type: Date, default: Date.now }
});

export default mongoose.model("Traffic", TrafficSchema);