import mongoose from "mongoose";
import bcrypt from "bcrypt";

const SALT_ROUNDS = 10;

const refreshTokenSchema = new mongoose.Schema({
  userId: { type: mongoose.Types.ObjectId, ref: "User", required: true },
  refreshTokenMetadata: { type: String, required: true },
  tokenHash: { type: String, required: true },
  timestamp: { type: Number, required: true },
  expiryDate: { type: Number, required: true },
});

refreshTokenSchema.pre("save", async function (next) {
  if (!this.isModified("tokenHash")) {
    return next();
  }
  const hash = await bcrypt.hash(this.tokenHash, SALT_ROUNDS);
  this.tokenHash = hash;
  next();
});

export default mongoose.model("RefreshToken", refreshTokenSchema);
