#include <Wire.h>
#include <INA3221.h>

INA3221 sensor(INA3221_ADDR40_GND);

const float SHUNT_RESISTANCE_OHM = 0.1;
const int ALERT_PIN = 13;             // LED/buzzer for theft alert
const float EPSILON = 1e-6;           // threshold for float zero-checks
const float THEFT_THRESHOLD_W = 0.5; // power difference to trigger alert (W)

struct ChannelData
{
  float busV;
  float shuntV;
  float current;
  float power;
  float supplyV;
  float loadR;
  float shuntR;
  float recomputedP;
  float dropRatio;
  float currentDensity;
  float conductance;
};

ChannelData readChannel(int ch)
{
  ChannelData d;

  float busVoltage_V = sensor.getVoltage(ch);
  // getShuntVoltage() returns millivolts (mV), not microvolts
  float shuntVoltage_mV = sensor.getShuntVoltage(ch);
  float shuntVoltage_V = shuntVoltage_mV / 1000.0;
  float current_A = shuntVoltage_V / SHUNT_RESISTANCE_OHM;

  d.busV = busVoltage_V;
  d.shuntV = shuntVoltage_V;
  d.current = current_A;
  d.power = busVoltage_V * current_A;

  d.supplyV = busVoltage_V + shuntVoltage_V;

  if(fabs(current_A) > EPSILON)
  {
    d.loadR = busVoltage_V / current_A;
    d.shuntR = shuntVoltage_V / current_A;
  }
  else
  {
    d.loadR = 0;
    d.shuntR = 0;
  }

  // recomputedP uses supply voltage (busV + shuntV) for cross-check
  d.recomputedP = d.supplyV * current_A;

  if(fabs(busVoltage_V) > EPSILON)
  {
    d.dropRatio = shuntVoltage_V / busVoltage_V;
    d.currentDensity = current_A / busVoltage_V;
  }
  else
  {
    d.dropRatio = 0;
    d.currentDensity = 0;
  }

  if(fabs(d.loadR) > EPSILON)
    d.conductance = 1.0 / d.loadR;
  else
    d.conductance = 0;

  return d;
}

void printChannel(ChannelData d, int ch)
{
  Serial.println("=====================================");

  Serial.print("CHANNEL: ");
  Serial.println(ch);

  Serial.print("1. Bus Voltage (V): ");
  Serial.println(d.busV,4);

  Serial.print("2. Shunt Voltage (V): ");
  Serial.println(d.shuntV,6);

  Serial.print("3. Current (A): ");
  Serial.println(d.current,6);

  Serial.print("4. Power (W): ");
  Serial.println(d.power,6);

  Serial.print("5. Supply Voltage (V): ");
  Serial.println(d.supplyV,6);

  Serial.print("6. Load Resistance (Ohm): ");
  Serial.println(d.loadR,6);

  Serial.print("7. Shunt Resistance (Ohm): ");
  Serial.println(d.shuntR,6);

  Serial.print("8. Recomputed Power (W): ");
  Serial.println(d.recomputedP,6);

  Serial.print("9. Voltage Drop Ratio: ");
  Serial.println(d.dropRatio,8);

  Serial.print("10. Current Density: ");
  Serial.println(d.currentDensity,8);

  Serial.print("11. Conductance (S): ");
  Serial.println(d.conductance,8);
}

void setup()
{
  Serial.begin(9600);
  Wire.begin();
  pinMode(ALERT_PIN, OUTPUT);
  digitalWrite(ALERT_PIN, LOW);

  sensor.begin();
  sensor.setShuntRes(100,100,100);

  Serial.println("INA3221 Power Theft Detection System");
}

void loop()
{
  ChannelData ch1 = readChannel(INA3221_CH1);
  delay(10);
  ChannelData ch2 = readChannel(INA3221_CH2);

  Serial.println("\nCHANNEL 1 (Input Side)");
  printChannel(ch1,1);

  Serial.println("\nCHANNEL 2 (Output Side)");
  printChannel(ch2,2);

  float inputPower = ch1.power;
  float outputPower = ch2.power;

  float powerError = inputPower - outputPower;

  float efficiency = 0;
  if(fabs(inputPower) > EPSILON)
  {
    efficiency = outputPower / inputPower;
    // clamp to [0, 1] — sensor noise can push slightly above 1
    if(efficiency > 1.0) efficiency = 1.0;
    if(efficiency < 0.0) efficiency = 0.0;
  }

  float currentDiff = ch1.current - ch2.current;

  Serial.println("\n========== System Analysis ==========");

  Serial.print("Input Power (CH1) (W): ");
  Serial.println(inputPower,6);

  Serial.print("Output Power (CH2) (W): ");
  Serial.println(outputPower,6);

  Serial.print("Power Error (W): ");
  Serial.println(powerError,6);

  Serial.print("Efficiency Ratio: ");
  Serial.println(efficiency,6);

  Serial.print("Efficiency (%): ");
  Serial.println(efficiency*100,2);

  Serial.print("Current Difference (CH1-CH2): ");
  Serial.println(currentDiff,6);

  // Theft alert: trigger if power loss exceeds threshold
  bool theftDetected = (powerError > THEFT_THRESHOLD_W);
  Serial.print("Theft Alert: ");
  Serial.println(theftDetected ? "YES" : "NO");
  digitalWrite(ALERT_PIN, theftDetected ? HIGH : LOW);

  Serial.println("=====================================");
  delay(3000);
}