import mongoose from "mongoose";
import bcrypt from "bcrypt";

const SALT_ROUNDS = 10;

const userSchema = new mongoose.Schema({
  username: { type: String, required: true, unique: true },
  passwordHash: { type: String, required: true },
  createTime: { type: Number, default: Date.now },
});

userSchema.methods.isValidPassword = async function (password) {
  return bcrypt.compare(password, this.passwordHash);
};

userSchema.pre("save", async function (next) {
  if (!this.isModified("passwordHash")) {
    return next();
  }
  const hash = await bcrypt.hash(this.passwordHash, SALT_ROUNDS);
  this.passwordHash = hash;
  next();
});

export default mongoose.model("User", userSchema);
