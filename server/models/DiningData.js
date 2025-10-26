import mongoose from "mongoose";

const DiningDataSchema = new mongoose.Schema({
  hallName: String,
  capacity: Number,
  currentOccupancy: Number,
  lastUpdated: { type: Date, default: Date.now }
});

export default mongoose.model("DiningData", DiningDataSchema);
