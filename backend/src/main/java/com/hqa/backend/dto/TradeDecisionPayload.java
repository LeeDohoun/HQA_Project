package com.hqa.backend.dto;

import java.util.ArrayList;
import java.util.List;

public class TradeDecisionPayload {

    private int totalScore;
    private String action = "";
    private String actionCode = "";
    private int confidence;
    private String riskLevel = "MEDIUM";
    private String riskLevelCode = "";
    private String summary = "";
    private List<String> keyCatalysts = new ArrayList<>();
    private List<String> riskFactors = new ArrayList<>();
    private String detailedReasoning = "";
    private String positionSize = "0%";
    private String entryStrategy = "";
    private String exitStrategy = "";
    private String stopLoss = "";
    private String signalAlignment = "";
    private String contrarianView = "";

    public int getTotalScore() { return totalScore; }
    public void setTotalScore(int totalScore) { this.totalScore = totalScore; }
    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }
    public String getActionCode() { return actionCode; }
    public void setActionCode(String actionCode) { this.actionCode = actionCode; }
    public int getConfidence() { return confidence; }
    public void setConfidence(int confidence) { this.confidence = confidence; }
    public String getRiskLevel() { return riskLevel; }
    public void setRiskLevel(String riskLevel) { this.riskLevel = riskLevel; }
    public String getRiskLevelCode() { return riskLevelCode; }
    public void setRiskLevelCode(String riskLevelCode) { this.riskLevelCode = riskLevelCode; }
    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }
    public List<String> getKeyCatalysts() { return keyCatalysts; }
    public void setKeyCatalysts(List<String> keyCatalysts) { this.keyCatalysts = keyCatalysts; }
    public List<String> getRiskFactors() { return riskFactors; }
    public void setRiskFactors(List<String> riskFactors) { this.riskFactors = riskFactors; }
    public String getDetailedReasoning() { return detailedReasoning; }
    public void setDetailedReasoning(String detailedReasoning) { this.detailedReasoning = detailedReasoning; }
    public String getPositionSize() { return positionSize; }
    public void setPositionSize(String positionSize) { this.positionSize = positionSize; }
    public String getEntryStrategy() { return entryStrategy; }
    public void setEntryStrategy(String entryStrategy) { this.entryStrategy = entryStrategy; }
    public String getExitStrategy() { return exitStrategy; }
    public void setExitStrategy(String exitStrategy) { this.exitStrategy = exitStrategy; }
    public String getStopLoss() { return stopLoss; }
    public void setStopLoss(String stopLoss) { this.stopLoss = stopLoss; }
    public String getSignalAlignment() { return signalAlignment; }
    public void setSignalAlignment(String signalAlignment) { this.signalAlignment = signalAlignment; }
    public String getContrarianView() { return contrarianView; }
    public void setContrarianView(String contrarianView) { this.contrarianView = contrarianView; }
}
